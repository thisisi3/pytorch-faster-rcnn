import torch, torchvision
import torch.nn as nn
import copy, random, logging
import numpy as np
import time, sys, os
import os.path as osp
from . import config, utils

#####################################################
### new implementation using vertorized computing ###
#####################################################

class AnchorCreator(object):
    '''
    It creates anchors based on image size(H, W) and feature size(h, w).
    
    Args:
        img_size: tuple of (H, W)
        grid: feature size, tupe of (h, w)
    Returns:
        anchors: a tensor of shapw (4, num_anchors, h, w)
    '''
    MAX_CACHE_ANCHOR = 1000
    CACHE_REPORT_PERIOD = 100
    def __init__(self, base=16, scales=[8, 16, 32],
                 aspect_ratios=[0.5, 1.0, 2.0], device=torch.device('cuda:0')):
        self.device = device
        self.base = base
        self.scales = scales
        self.aspect_ratios = aspect_ratios
        self.cached = {}
        self.count = 0
        anchor_ws, anchor_hs = [], []
        for s in scales:
            for ar in aspect_ratios:
                anchor_ws.append(base * s * np.sqrt(ar))
                anchor_hs.append(base * s / np.sqrt(ar))
        self.anchor_ws = torch.tensor(anchor_ws, device=device, dtype=torch.float32)
        self.anchor_hs = torch.tensor(anchor_hs, device=device, dtype=torch.float32)

    def to(self, device):
        self.device = device
        self.anchor_ws.to(device)
        self.anchor_hs.to(device)

    def report_cache(self):
        count_info = [[k, v[0]] for k,v in self.cached.items()]
        count_info.sort(key=lambda x:x[1], reverse=True)
        top_count = count_info[:10]
        top_str = ', '.join([':'.join([str_id, str(ct)]) for str_id, ct in top_count])
        rep_str = '\n'.join([
            'AnchorCreator count: {}'.format(self.count),
            'Cache size: {}'.format(len(self.cached)),
            'Top 10 used anchor count: {}'.format(top_str)
        ])
        logging.info(rep_str)

    def __call__(self, img_size, grid):
        str_id = '|'.join([
            ','.join([str(x) for x in img_size]),
            ','.join([str(x) for x in grid])
        ])
        # check if the anchor is in the cached
        if str_id in self.cached:
            self.cached[str_id][0] += 1
            return self.cached[str_id][1]
        anchors = self._create_anchors_(img_size, grid)
        if len(self.cached) < self.MAX_CACHE_ANCHOR:
            self.cached[str_id] = [1, anchors]
        self.count += 1
        if self.count % self.CACHE_REPORT_PERIOD == 0:
            self.report_cache()
        return anchors
        
    def _create_anchors_(self, img_size, grid):
        assert len(img_size) == 2 and len(grid) == 2
        imag_h, imag_w = img_size
        grid_h, grid_w = grid
        grid_dist_h, grid_dist_w = imag_h/grid_h, imag_w/grid_w
        
        center_h = torch.linspace(0, imag_h, grid_h+1,
                                  device=self.device, dtype=torch.float32)[:-1] + grid_dist_h/2
        center_w = torch.linspace(0, imag_w, grid_w+1,
                                  device=self.device, dtype=torch.float32)[:-1] + grid_dist_w/2
        mesh_h, mesh_w = torch.meshgrid(center_h, center_w)
        # NOTE that the corresponding is h <-> y and w <-> x
        anchor_hs = self.anchor_hs.view(-1, 1, 1)
        anchor_ws = self.anchor_ws.view(-1, 1, 1)
        x_min = mesh_w - anchor_ws / 2
        x_max = mesh_w + anchor_ws / 2
        y_min = mesh_h - anchor_hs / 2
        y_max = mesh_h + anchor_hs / 2
        anchors = torch.stack([x_min, y_min, x_max, y_max])
        return anchors

def inside_anchor_mask(anchors, img_size):
    H, W = img_size
    inside = (anchors[0,:]>=0) & (anchors[1,:]>=0) & \
             (anchors[2,:]<=W) & (anchors[3,:]<=H)
    return inside

def random_sample_label(labels, pos_num, tot_num):
    assert pos_num <= tot_num
    pos_args = utils.index_of(labels==1)
    if len(pos_args[0]) > pos_num:
        dis_idx = np.random.choice(
            pos_args[0].cpu().numpy(), size=(len(pos_args[0]) - pos_num), replace=False)
        labels[dis_idx] = -1
    real_n_pos = min(len(pos_args[0]), pos_num)
    n_negs = tot_num - real_n_pos
    neg_args = utils.index_of(labels==0)
    if len(neg_args[0]) > n_negs:
        dis_idx = np.random.choice(
            neg_args[0].cpu().numpy(), size=(len(neg_args[0]) - n_negs), replace=False)
        labels[dis_idx] = -1
    return labels

class AnchorTargetCreator(object):
    '''
    It assigns gt bboxes to anchors based on some rules.
    Args:
        anchors: tensor of shape (4, n), n is number of anchors
        gt_bbox: tensor of shape (4, m), m is number of gt bboxes
    Returns:
        labels: consists of 1=positive anchor, 0=negative anchor, -1=ignore
        params: bbox adjustment values which will be regressed
        bbox_labels: gt bboxes assigned to each anchor
    '''
    def __init__(self, pos_iou=0.7, neg_iou=0.3, max_pos=128, max_targets=256):
        self.pos_iou = pos_iou
        self.neg_iou = neg_iou
        self.max_pos = max_pos
        self.max_targets = max_targets

    def __call__(self, anchors, gt_bbox):
        assert anchors.shape[0] == 4 and gt_bbox.shape[0] == 4
        # TODO: find out why there is a diff btw old and new version
        with torch.no_grad():
            gt_bbox = gt_bbox.to(torch.float32)
            n_anchors, n_gts = anchors.shape[1], gt_bbox.shape[1]
            labels = torch.full((n_anchors,), -1, device=anchors.device, dtype=torch.int)
            iou_tab = utils.calc_iou(anchors, gt_bbox)
            max_anchor_iou, max_anchor_arg = torch.max(iou_tab, dim=0)
            max_gt_iou, max_gt_arg = torch.max(iou_tab, dim=1)
            # first label negative anchors, some of them might be replaced with positive later
            labels[(max_gt_iou < self.neg_iou)] = 0
            # next label positive anchors
            labels[max_anchor_arg] = 1
            labels[(max_gt_iou >= self.pos_iou)] = 1
            # chose anchor has the same max iou with GT as positive
            equal_max_anchor = (iou_tab == max_anchor_iou)
            equal_max_anchor_idx = (equal_max_anchor.sum(1) > 0)
            labels[equal_max_anchor_idx] = 1

            labels = random_sample_label(labels, self.max_pos, self.max_targets)
            bbox_labels = gt_bbox[:,max_gt_arg]
        return labels, bbox_labels
        

class ProposalCreator(object):
    '''
    It propose regions that potentially contain objects.
    Args:
        rpn_cls_out: output of the classifer of RPN
        rpn_reg_out: output of the regressor of RPN
        anchors: (4, n) where n is number of anchors
    Returns:
        props_bbox: tensor of shape (4, n)
        top_scores: objectness score
    '''
    def __init__(self, max_pre_nms, max_post_nms, nms_iou, min_size):
        self.max_pre_nms = max_pre_nms
        self.max_post_nms = max_post_nms
        self.nms_iou = nms_iou
        self.min_size = min_size

    def __call__(self, rpn_cls_out, rpn_reg_out, anchors, img_size, scale=1.0):
        assert anchors.shape[0] == 4 and len(anchors.shape) == 2
        n_anchors = anchors.shape[1]
        min_size = scale * self.min_size # this is the value from simple-faster-rcnn
        #min_size = -1 # this is the old version value which is basically do not filter,
                      # but will filter zero crops in ROI pooling layer
        H, W = img_size
        with torch.no_grad():
            cls_out = rpn_cls_out.view(2, -1)
            reg_out = rpn_reg_out.view(4, -1)
            scores = torch.softmax(cls_out, 0)[1]
            props_bbox = utils.param2bbox(anchors, reg_out)
            props_bbox = torch.stack([
                torch.clamp(props_bbox[0], 0.0, W),
                torch.clamp(props_bbox[1], 0.0, H),
                torch.clamp(props_bbox[2], 0.0, W),
                torch.clamp(props_bbox[3], 0.0, H)
            ])
            small_area_mask = (props_bbox[2] - props_bbox[0] < min_size) \
                              | (props_bbox[3] - props_bbox[1] < min_size)
            num_small_area = small_area_mask.sum()
            scores[small_area_mask] = -1
            sort_args = torch.argsort(scores, descending=True)
            if num_small_area > 0:
                sort_args = sort_args[:-num_small_area]
            top_sort_args = sort_args[:self.max_pre_nms]
            
            props_bbox = props_bbox[:, top_sort_args]
            top_scores = scores[top_sort_args]
            keep = torchvision.ops.nms(props_bbox.t(), top_scores, self.nms_iou)
            keep = keep[:self.max_post_nms]
        return props_bbox[:, keep], top_scores[keep]
        

class ProposalTargetCreator(object):
    """
    Choose regions to train RCNN.
    Args:
        props_bbox: region proposals with shape (4, n) where n=number of regions
        gt_bbox: gt bboxes with shape (4, m) where m=number of gt bboxes
        gt_label: gt lables with shape (4,) where m=number of labels
    Returns:
        props_bbox: chosen bbox
        roi_label: class labels of each chosen roi
        roi_gt_bbox: gt assigned to each props_bbox
    """
    def __init__(self,
                 max_pos=32,
                 max_targets=128,
                 pos_iou=0.5,
                 neg_iou_hi=0.5,
                 neg_iou_lo=0.0):
        self.max_pos = max_pos
        self.max_targets = max_targets
        self.pos_iou = pos_iou
        self.neg_iou_hi = neg_iou_hi
        self.neg_iou_lo = neg_iou_lo
        self.param_normalize_mean = (0.0, 0.0, 0.0, 0.0)
        self.param_normalize_std  = (0.1, 0.1, 0.2, 0.2)
        
    def __call__(self, props_bbox, gt_bbox, gt_label):
        with torch.no_grad():
            gt_bbox = gt_bbox.to(props_bbox.dtype)
            # add gt to train RCNN
            props_bbox = torch.cat([gt_bbox, props_bbox], dim=1)
            n_props, n_gts = props_bbox.shape[1], gt_bbox.shape[1]
            iou_tab = utils.calc_iou(props_bbox, gt_bbox)
            max_gt_iou, max_gt_arg = torch.max(iou_tab, dim=1)
            label = torch.full((n_props,), -1, device = props_bbox.device, dtype=torch.int)
            label[max_gt_iou >= self.pos_iou] = 1
            label[(max_gt_iou < self.neg_iou_hi) & (max_gt_iou >= self.neg_iou_lo)] = 0
            label = random_sample_label(label, self.max_pos, self.max_targets)
            pos_idx, neg_idx = (label==1), (label==0)
            chosen_idx = pos_idx | neg_idx
            # just for logging purpose
            chosen_iou = max_gt_iou[chosen_idx]
            logging.debug('ProposalTargetCreator: max_iou={}, min_iou={}'.format(
                chosen_iou.max(), chosen_iou.min()))
            # find class label of each roi, 0 is background
            roi_label = gt_label[max_gt_arg]
            roi_label[neg_idx] = 0
            # find gt bbox for each roi
            roi_gt_bbox = gt_bbox[:,max_gt_arg]
            #roi_param = utils.bbox2param(props_bbox, roi_gt_bbox)
            #param_mean = roi_param.new(self.param_normalize_mean)
            #param_std  = roi_param.new(self.param_normalize_std)
            #roi_param = (roi_param - param_mean.view(4, 1))/param_std.view(4, 1)
        # next only choose rois of non-negative
        return props_bbox[:,chosen_idx], roi_label[chosen_idx], roi_gt_bbox[:, chosen_idx]


def image2feature(bbox, img_size, feat_size):
    """
    transfer bbox size from image to feature
    """
    h_rat, w_rat = [feat_size[i]/img_size[i] for i in range(2)]
    return bbox * torch.tensor([[w_rat], [h_rat], [w_rat], [h_rat]],
                               device=bbox.device, dtype=torch.float32)
    
class ROICropping(object):
    def __init__(self):
        pass

    def __call__(self, feature, props, image_size):
        if props.numel() == 0:
            logging.warning('ROICropping reveives zero proposals')
            return []
        _, n_chanel, h, w = feature.shape
        feat_size = feature.shape[-2:]
        # process of cropping participates in the computation graph
        bbox_feat = image2feature(props, image_size, feat_size).round().int()
        crops = [feature[0, :, y_min:y_max+1, x_min:x_max+1] \
                 for x_min, y_min, x_max, y_max in bbox_feat.t()]
        return crops


class ROIPooling(nn.Module):
    def __init__(self, out_size):
        super(ROIPooling, self).__init__()
        self.out_size = out_size
        self.adaptive_pool = nn.AdaptiveMaxPool2d(out_size)
        
    def forward(self, rois):
        if len(rois) == 0:
            logging.warning('ROIPooling receives an empty list of rois')
            return None
        zero_area, pos_area = [], []
        for roi in rois:
            if roi.numel()==0:
                zero_area.append(roi)
            else:
                pos_area.append(roi)
        if len(zero_area)>0:
            logging.warning('Encounter'
                            ' {} rois with 0 area, ignore this batch!'.format(len(zero_area)))
            return None
        if len(pos_area)==0:
            logging.warning('No rois with positive area, ignore this batch!')
            return None
        return torch.stack([self.adaptive_pool(x) for x in pos_area])
