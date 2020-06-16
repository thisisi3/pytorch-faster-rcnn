from torch import nn
from mmcv.cnn import normal_init
import numpy as np
import logging, torch

from .. import utils, debug, region


def make_level_blanks(grids, dim, value, dtype, device):
    return [torch.full(list(grid)+[dim], value, dtype=dtype, device=device) \
            for grid in grids]

def positive_ltrb(ltrb):
    pos_mask = ltrb > 0
    return pos_mask.all(dim=-1)

def centerness(ltrb):
    ltrb = ltrb + 1e-6
    l, t, r, b = [ltrb[..., i] for i in range(4)]
    return torch.sqrt((torch.min(l, r)/torch.max(l, r))*(torch.min(t, b)/torch.max(t, b)))

def ltrb2bbox(ltrb, stride):
    grid_size = ltrb.shape[1:]
    full_idx = utils.full_index(grid_size).to(
        device=ltrb.device, dtype=ltrb.dtype)
    
    coor = full_idx * stride + stride / 2
    bbox = torch.stack([
        coor[:, :, 1] - ltrb[0, :, :],
        coor[:, :, 0] - ltrb[1, :, :],
        ltrb[2, :, :] + coor[:, :, 1],
        ltrb[3, :, :] + coor[:, :, 0]
    ])
    return bbox

class FCOSHead(nn.Module):
    def __init__(self,
                 num_classes=21,
                 in_channels=256,
                 stacked_convs=4,
                 feat_channels=256,
                 strides=[8, 16, 32, 64, 126],
                 scale=8,
                 reg_std=300,
                 center_neg_ratio=3,
                 loss_cls=None,
                 loss_bbox=None,
                 loss_centerness=None):
        super(FCOSHead, self).__init__()
        self.num_classes=num_classes
        self.cls_channels=num_classes-1
        self.in_channels=in_channels
        self.stacked_convs=stacked_convs
        self.feat_channels=feat_channels
        self.strides=strides
        self.scale=scale
        self.center_neg_ratio=center_neg_ratio
        self.reg_std=reg_std
        from ..builder import build_module
        self.loss_cls=build_module(loss_cls)
        self.loss_bbox=build_module(loss_bbox)
        self.loss_centerness=build_module(loss_centerness)

        self.init_layers()
        
    def init_layers(self):
        cls_convs = []
        reg_convs = []
        for i in range(self.stacked_convs):
            chn = self.in_channels if i == 0 else self.feat_channels
            cls_convs.append(
                nn.Conv2d(chn, self.feat_channels, 3, padding=1))
            cls_convs.append(nn.ReLU(inplace=True))
            reg_convs.append(
                nn.Conv2d(chn, self.feat_channels, 3, padding=1))
            reg_convs.append(nn.ReLU(inplace=True))
        self.cls_convs = nn.Sequential(*cls_convs)
        self.reg_convs = nn.Sequential(*reg_convs)
        self.fcos_cls = nn.Conv2d(
            self.feat_channels,
            self.cls_channels,
            3,
            padding=1)
        self.fcos_reg = nn.Conv2d(
            self.feat_channels, 4, 3, padding=1)
        self.fcos_center = nn.Conv2d(
            self.feat_channels, 1, 3, padding=1)
        self.reg_coef = nn.Parameter(
            torch.tensor([1.0/srd for srd in self.strides], dtype=torch.float))

    def init_weights(self):
        for m in self.cls_convs:
            if isinstance(m, nn.Conv2d):
                normal_init(m, std=0.01)
        for m in self.reg_convs:
            if isinstance(m, nn.Conv2d):
                normal_init(m, std=0.01)
        prior_prob = 0.01
        bias_init = float(-np.log((1-prior_prob)/ prior_prob))
        normal_init(self.fcos_cls, std=0.01, bias=bias_init)
        normal_init(self.fcos_reg, std=0.01)
        normal_init(self.fcos_center, std=0.01)
        logging.info('Initialized weights for RetinaHead.')

    def forward(self, xs):
        cls_conv_outs = [self.cls_convs(x) for x in xs]
        reg_conv_outs = [self.reg_convs(x) for x in xs]
        cls_outs = [self.fcos_cls(x) for x in cls_conv_outs]
        reg_outs = [self.fcos_reg(x) for x in reg_conv_outs]
        ctr_outs = [self.fcos_center(x) for x in reg_conv_outs]
        return cls_outs, reg_outs, ctr_outs


    def single_image_targets(self, cls_outs, reg_outs, ctr_outs,
                             gt_bboxes, gt_labels, img_meta, train_cfg):
        gt_bboxes, gt_labels = utils.sort_bbox(gt_bboxes, labels=gt_labels, descending=True)
        gt_lvl = region.map_gt2level(self.scale, self.strides, gt_bboxes)
        device = cls_outs[0].device
        grids = [x.shape[-2:] for x in cls_outs]

        cls_tars = make_level_blanks(grids, 1, 0, dtype=torch.long, device=device)
        reg_tars = make_level_blanks(grids, 4, 0, dtype=torch.float, device=device)
        ctr_tars = []

        num_gt = gt_bboxes.shape[1]
        num_lvl = len(self.strides)
        # make ltrb matrix for all gt
        ltrb_list = []
        for i in range(num_gt):
            lvl = gt_lvl[i]
            bbox = gt_bboxes[:, i]
            full_idx = utils.full_index(grids[lvl]).to(device=device)
            coor = full_idx * self.strides[lvl] + self.strides[lvl]/2
            coor = coor.to(dtype=torch.float)
            ltrb = torch.stack([
                coor[:, :, 1] - bbox[0],
                coor[:, :, 0] - bbox[1],
                bbox[2] - coor[:, :, 1],
                bbox[3] - coor[:, :, 0]
            ], dim=-1)
            ltrb_list.append(ltrb)

        # find cls tars
        for i in range(num_gt):
            lvl = gt_lvl[i]
            bbox = gt_bboxes[:, i] / self.strides[lvl]
            bbox = bbox.round().int()
            cls_tars[lvl][bbox[1]:bbox[3], bbox[0]:bbox[2]] = gt_labels[i]
        
        # find reg tars
        for i in range(num_gt):
            lvl = gt_lvl[i]
            pos_ltrb = positive_ltrb(ltrb_list[i])
            reg_tars[lvl][pos_ltrb] = ltrb_list[i][pos_ltrb]

        pos_ltrb_list = [positive_ltrb(x).unsqueeze(-1) for x in reg_tars]
        
        # find center tars
        for i in range(num_lvl):
            cur_ctr = centerness(reg_tars[i])
            cur_ctr = cur_ctr.unsqueeze(-1)
            cur_ctr[~pos_ltrb_list[i]] = 0
            ctr_tars.append(cur_ctr)

        return cls_tars, reg_tars, ctr_tars, pos_ltrb_list

    def calc_loss(self, cls_outs, reg_outs, ctr_outs, cls_tars, reg_tars, ctr_tars, pos_ltrb):
        logging.debug('IN Calculation Loss'.center(50, '*'))
        logging.debug('reg_coef: {}'.format(self.reg_coef.tolist()))

        
        # first transform reg_outs
        for i in range(len(reg_outs)):
            for j in range(len(reg_outs[i])):
                reg_outs[i][j] = torch.exp(reg_outs[i][j]*self.reg_coef[j])
        
        # combine targets from all images and calculate loss at once

        num_imgs = len(cls_tars)
        cls_outs = torch.cat([utils.concate_grid_result(x, False) for x in cls_outs], dim=-1)
        reg_outs = torch.cat([utils.concate_grid_result(x, False) for x in reg_outs], dim=-1)
        ctr_outs = torch.cat([utils.concate_grid_result(x, False) for x in ctr_outs], dim=-1)
        cls_tars = torch.cat([utils.concate_grid_result(x, True)  for x in cls_tars], dim=0)
        reg_tars = torch.cat([utils.concate_grid_result(x, True)  for x in reg_tars], dim=0)
        ctr_tars = torch.cat([utils.concate_grid_result(x, True)  for x in ctr_tars], dim=0)
        pos_ltrb = torch.cat([utils.concate_grid_result(x, True)  for x in pos_ltrb], dim=0)

        # first calc cls loss

        pos_cls = (cls_tars>0).sum().item()
        cls_loss = self.loss_cls(cls_outs.t(), cls_tars.squeeze()) / pos_cls
        
        # second calc reg loss
        pos_ltrb = pos_ltrb.squeeze()
        pos_reg = (pos_ltrb>0).sum().item()
        pos_reg_out = reg_outs[:, pos_ltrb]  # [4, m]
        pos_reg_tar = reg_tars[pos_ltrb, :] / self.reg_std  # [m, 4]

        reg_loss = self.loss_bbox(pos_reg_out, pos_reg_tar.t()) / pos_reg

        # next calc ctr loss
        ctr_tars = ctr_tars.squeeze() # [n]
        ctr_outs = ctr_outs.view(-1, 1) # [n, 1]
        pos_ctr_places = ctr_tars>0
        pos_ctr = (pos_ctr_places).sum().item()
        neg_ctr_places = (ctr_tars==0)
        neg_ctr = (neg_ctr_places).sum().item()
        neg_allowed = self.center_neg_ratio * pos_ctr
        if neg_ctr > neg_allowed:
            chosen_neg_inds = utils.random_select(neg_ctr_places.nonzero(), neg_allowed)
            chosen_pos_inds = pos_ctr_places.nonzero()
            chosen_places = torch.cat([chosen_neg_inds, chosen_pos_inds])
            ctr_tars = ctr_tars[chosen_places.squeeze()]
            ctr_outs = ctr_outs[chosen_places.squeeze()]
        
        ctr_loss = self.loss_centerness(ctr_outs, ctr_tars) / ctr_tars.numel()
        logging.debug('pos ctr samples: {}, total ctr samples: {}'.format(
            pos_ctr, ctr_tars.numel()))
        logging.debug('Positive count: pos_cls={}, pos_reg={}, pos_ctr={}'.format(
            pos_cls, pos_reg, pos_ctr))
        return {'cls_loss': cls_loss, 'reg_loss': reg_loss, 'ctr_loss': ctr_loss}

    def loss(self):
        # 1, find targets
        # 2, calc loss
        pass

    # main interface for detector, it returns fcos head loss as a dict
    # loss: cls_loss, reg_loss, centerness_loss
    def forward_train(self, feats, gt_bboxes, gt_labels, img_metas, train_cfg):
        # forward data and calc loss
        cls_outs, reg_outs, ctr_outs = self.forward(feats)
        cls_outs_img = utils.split_by_image(cls_outs)
        reg_outs_img = utils.split_by_image(reg_outs)
        ctr_outs_img = utils.split_by_image(ctr_outs)

        tars = utils.unpack_multi_result(utils.multi_apply(
            self.single_image_targets,
            cls_outs_img,
            reg_outs_img,
            ctr_outs_img,
            gt_bboxes,
            gt_labels,
            img_metas,
            train_cfg))
        tars = [cls_outs_img, reg_outs_img, ctr_outs_img] + list(tars)
        return self.calc_loss(*tars)

    def predict_single_image(self, cls_outs, reg_outs, ctr_outs, img_meta, test_cfg):
        num_lvl = len(cls_outs)

        # next calc bbox
        min_size = img_meta['scale_factor'] * test_cfg.min_bbox_size
        img_size = img_meta['img_shape'][:2]
        assert num_lvl == len(self.strides)
        bboxes, scores = [], []
        for i in range(num_lvl):
            reg_outs[i] = torch.exp(reg_outs[i] * self.reg_coef[i]) * self.reg_std
            bbox = ltrb2bbox(reg_outs[i], self.strides[i])
            score = cls_outs[i].sigmoid() * ctr_outs[i].sigmoid()
            bbox = bbox.view(4, -1)
            score = score.view(self.cls_channels, -1)
            
            bbox = utils.clamp_bbox(bbox, img_size)
            non_small = (bbox[2]-bbox[0] + 1>min_size) & (bbox[3]-bbox[1]+1>min_size)
            score = score[:, non_small]
            bbox = bbox[:, non_small]
            if test_cfg.pre_nms > 0 and test_cfg.pre_nms < score.shape[1]:
                max_score, _ = score.max(0)
                _, top_inds = max_score.topk(test_cfg.pre_nms)
                score = score[:, top_inds]
                bbox = bbox[:, top_inds]
            bboxes.append(bbox)
            scores.append(score)
        mlvl_score = torch.cat(scores, dim=1)
        mlvl_bbox  = torch.cat(bboxes, dim=1)
        nms_label_set = list(range(0, self.cls_channels))
        label_adjust = 1
        if 'nms_type' not in test_cfg:
            nms_op = utils.multiclass_nms_mmdet
        elif test_cfg.nms_type == 'official':
            nms_op = utils.multiclass_nms_mmdet
        elif test_cfg.nms_type == 'strict':
            nms_op = utils.multiclass_nms_v2
        else:
            raise ValueError('Unknown nms_type: {}'.format(test_cfg.nms_type))
        keep_bbox, keep_score, keep_label = nms_op(
            mlvl_bbox.t(), mlvl_score.t(), nms_label_set,
            test_cfg.nms_iou, test_cfg.min_score, test_cfg.max_per_img)
        keep_label += label_adjust
        return keep_bbox.t(), keep_score, keep_label
            
                

    # main interface for detector, for testing
    def predict_bboxes(self, feats, img_metas, test_cfg):
        cls_outs, reg_outs, ctr_outs = self.forward(feats)
        cls_outs_img = utils.split_by_image(cls_outs)
        reg_outs_img = utils.split_by_image(reg_outs)
        ctr_outs_img = utils.split_by_image(ctr_outs)
        
        pred_res =utils.unpack_multi_result(utils.multi_apply(
            self.predict_single_image,
            cls_outs_img,
            reg_outs_img,
            ctr_outs_img,
            img_metas,
            test_cfg))
        return pred_res

    def predict_bboxes_from_output(self):
        pass
