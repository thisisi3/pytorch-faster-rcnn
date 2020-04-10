from torch import nn
from ..utils import init_module_normal, class_name
from ..bbox import bbox_target
from .. import losses
from .. import utils

import logging, torch
from mmcv.cnn import normal_init

# The input of a BBoxHead is proposals and rois of feature maps. It classifies proposals and regresses bbox adjustment.
class BBoxHead_v2(nn.Module):
    def __init__(self,
                 num_classes,
                 use_sigmoid=False,
                 target_means=[0.0, 0.0, 0.0, 0.0],
                 target_stds=[0.1, 0.1, 0.2, 0.2],
                 reg_class_agnostic=False,
                 loss_cls=None,
                 loss_bbox=None):
        super(BBoxHead_v2, self).__init__()
        self.num_classes=num_classes
        self.use_sigmoid=use_sigmoid
        if use_sigmoid:
            self.cls_channels=num_classes-1
        else:
            self.cls_channels=num_classes
        self.target_means=target_means
        self.target_stds=target_stds
        self.reg_class_agnostic=reg_class_agnostic
        from ..registry import build_module
        self.loss_cls=loss_cls
        if isinstance(loss_cls, dict):
            self.loss_cls=build_module(loss_cls)
        self.loss_bbox=loss_bbox
        if isinstance(loss_bbox, dict):
            self.loss_bbox=build_module(loss_bbox)
        
    def init_layers(self):
        raise NotImplementedError('init_layers is not implemented')
    
    def init_weights(self):
        raise NotImplementedError('init_weights is not implemented')
    
    # returns cls_outs and reg_outs which may be multi-image
    def forward(self):
        raise NotImplementedError('forward is not implemented')

    # find targets in each image and return a list of results
    # for each img, it returns tar_props, tar_bbox, tar_label, tar_param, tar_is_gt
    def bbox_targets(self, img_props, gt_bboxes, gt_labels, train_cfg):
        target_means = tuple(self.target_means)
        target_stds = tuple(self.target_stds)
        return utils.unpack_multi_result(
            utils.multi_apply(bbox_target, img_props, gt_bboxes, gt_labels,
                              train_cfg.assigner, train_cfg.sampler, target_means, target_stds))

    # simple calc loss from targets and avg_factor, all inputs are assumed concated results from diff imgs
    # in the case reg_class_agnostic=False, cls channels in regressor equals self.num_classes
    def calc_loss_all(self, cls_out, reg_out, tar_label, tar_param, train_cfg):
        logging.debug(' {}: calculate loss '.format(class_name(self)).center(50, '*'))
        device = cls_out.device
        cls_loss, reg_loss = losses.zero_loss(device), losses.zero_loss(device)
        pos_tars = tar_label>0
        n_samps = len(tar_label)
        avg_factor = pos_tars.sum() if 'sampler' not in train_cfg else n_samps
        logging.debug('pos:{}, neg:{}, avg_factor: {}'.format(
            pos_tars.sum().item(), (tar_label==0).sum().item(), avg_factor))
        if avg_factor == 0:
            logging.warning('return zero loss due to zero avg_factor')
            return cls_loss, reg_loss
        if tar_label.numel() != 0:
            cls_loss = self.loss_cls(cls_out, tar_label) / avg_factor
            if not self.reg_class_agnostic:
                reg_out = reg_out.view(-1, 4, self.num_classes)
                reg_out = reg_out[torch.arange(n_samp), :, tar_label]
            if pos_tars.sum() == 0:
                logging.warning('BBoxHead recieves no positive samples to train')
            else:
                pos_reg = reg_out[pos_tars, :]
                reg_loss = self.loss_bbox(pos_reg, tar_param[:, pos_tars].t()) / avg_factor
        else:
            logging.warning('BBoxHead received no samples to train, return dummy losses')
        return cls_loss, reg_loss

    def calc_loss(self, cls_outs, reg_outs, tar_labels, tar_params, train_cfg):
        cls_out = torch.cat(cls_outs, dim=0)
        reg_out = torch.cat(reg_outs, dim=0)
        tar_label = torch.cat(tar_labels)
        tar_param = torch.cat(tar_params, dim=1)
        return self.calc_loss_all(cls_out, reg_out, tar_label, tar_param, train_cfg)

    # calculate all losses in one step
    def loss(self, img_tars, train_cfg):
        pass

    def refine_bboxes(self, props, labels, reg_outs, is_gts=None, img_metas=None):
        return utils.multi_apply(self.refine_bboxes_single_image, props, labels, reg_outs, is_gts, img_metas)
        

    # only refine bboxes, will ignore gt
    # input is concated results from diff imgs
    def refine_bboxes_single_image(self, props, label, reg_out, is_gt=None, img_meta=None):
        logging.info(' {}: refine bboxes '.format(class_name(self)).center(50, '*'))
        if is_gt is None:
            is_gt = torch.full_like(label, 0)
        assert props.shape[1] == reg_out.shape[0] == is_gt.numel()
        logging.debug('number of gt in bboxes: {}'.format(is_gt.sum().item()))
        logging.debug('label: 0:{}, >0:{}'.format(
            (label==0).sum().item(), (label>0).sum().item()))
        n_samps = len(label)
        if not self.reg_class_agnostic:
            reg_out = reg_out.view(-1, 4, self.num_classes)
            reg_out = reg_out[torch.arange(n_samps), :, label]
        is_gt = is_gt.bool()
        props = props[:, ~is_gt]
        reg_out = reg_out.t()[:, ~is_gt]
        param_mean = reg_out.new(self.target_means).view(4, -1)
        param_std  = reg_out.new(self.target_stds).view(4, -1)
        reg_out = reg_out * param_std + param_mean
        refined = utils.param2bbox(props, reg_out)
        if img_meta is not None:
            H, W = img_meta['img_shape'][:2]
            refined[[0, 2], :].clamp_(0.0, W-1)
            refined[[1, 3], :].clamp_(0.0, H-1)
        return refined

    def predict_bboxes_single_image(self, props, cls_out, reg_out, img_size=None, cfg=None):
        with torch.no_grad():
            n_props = cls_out.shape[0]
            if self.use_sigmoid:
                sig = cls_out.sigmoid()
                score, label = sig.max(dim=1)
                label += 1
            else:
                soft = torch.softmax(cls_out, dim=1)
                score, label = soft.max(dim=1)
            if self.reg_class_agnostic:
                reg_out = reg_out.view(-1, 4)
            else:
                reg_out = reg_out.view(-1, 4, self.num_classes)
                reg_out = reg_out[torch.arange(n_props), :, label]
            param_mean = reg_out.new(self.target_means).view(-1, 4)
            param_std  = reg_out.new(self.target_stds).view(-1, 4)
            reg_out = reg_out * param_std + param_mean
            preds = utils.param2bbox(props, reg_out.t())
            if img_size is not None:
                H, W = img_size
                preds[[0, 2], :].clamp_(0.0, W-1)
                preds[[1, 3], :].clamp_(0.0, H-1)
            if cfg is not None:
                preds, score, label = utils.multiclass_nms(
                    preds, score, label, range(1, self.num_classes), cfg.nms_iou, cfg.min_score)
        return preds, score, label
            
        
    # roi_outs: list[tensor] roi outputs of different imgs
    # data of multi-images
    def predict_bboxes(self, roi_outs, props, img_metas=None, cfg=None):
        if img_metas is None:
            img_sizes = [None, None]
        else:
            img_sizes = [img_meta['img_shape'][:2] for img_meta in img_metas]
        cls_outs, reg_outs = self.forward(roi_outs)
        return utils.unpack_multi_result(
            utils.muilti_apply(self.predict_bboxes_single_image, props, cls_outs, reg_outs, img_sizes, cfg))
        
