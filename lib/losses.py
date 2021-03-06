import torch.nn as nn
import torch, logging
import torch.nn.functional as F
from . import utils
import numpy as np

def iou_loss(a, b):
    assert a.shape == b.shape and a.shape[0]==b.shape[0]==4
    iou = utils.elem_iou(a, b)
    return -iou.log()
    
# pred [4, n], gt [4, n]
def giou_loss(a, b):
    assert a.shape[0] == 4 and b.shape[0] == 4 and a.shape == b.shape
    tl = torch.max(a[:2], b[:2])
    br = torch.min(a[2:], b[2:])
    area_i = torch.prod(br-tl, dim=0)
    area_i = area_i * (tl<br).all(0).float()
    area_a = torch.prod(a[2:]-a[:2], dim=0)
    area_b = torch.prod(b[2:]-b[:2], dim=0)
    area_u = area_a + area_b - area_i
    iou = area_i / area_u
    convex = torch.stack([
        torch.min(a[0], b[0]),
        torch.min(a[1], b[1]),
        torch.max(a[2], b[2]),
        torch.max(a[3], b[3])])
    area_convex = torch.prod(convex[2:] - convex[:2], dim=0)
    giou = iou - (area_convex - area_u) / area_convex
    return 1 - giou


def sigmoid_focal_loss(pred, target, alpha=0.25, gamma=2.0, fix_alpha=False):
    '''
    Args:
        pred: [n, num_cls]
        target: [n], where 0 means background
    '''
    logging.debug('sigmoid_focal_loss'.center(50, '='))
    logging.debug('alpha={}, gamma={}'.format(alpha, gamma))
    n_samp, n_cls = pred.size()
    logging.debug('pred: {}, target: {}, unique vals of target: {}'\
                  .format(pred.shape, target.shape, target.unique()))
    logging.debug('check pred logits, max={}, min={}, mean={}'.format(pred.max(), pred.min(), pred.mean()))
    tar_one_hot  = utils.one_hot_embedding(target, n_cls+1) # [n, num_cls+1]
    tar_one_hot  = tar_one_hot[:, 1:]  # [n, num_cls]
    logging.debug('target 1:{}, 0:{}(/20)'.format((tar_one_hot==1).sum(), (tar_one_hot==0).sum()/20))
    pred_sigmoid  = pred.sigmoid()
    tar_one_hot = tar_one_hot.to(dtype=pred_sigmoid.dtype)
    pt = pred_sigmoid * tar_one_hot + (1-pred_sigmoid) * (1-tar_one_hot)
    logging.debug('checking pt, pt.numel()={}, pt==0:{}'.format(pt.numel(), (pt==0).sum()))
    if fix_alpha:
        focal_weight = alpha
    else:
        focal_weight  = alpha*tar_one_hot + (1-alpha)*(1-tar_one_hot)
    focal_weight  = focal_weight * (1-pt).pow(gamma)
    # debug_positive_target(target, p, t)
    logging.debug('focal weight max={}, min={}, mean={}'.format(
        focal_weight.max(), focal_weight.min(), focal_weight.mean()))
    focal_loss = F.binary_cross_entropy_with_logits(pred, tar_one_hot, reduction='none') * focal_weight
    return focal_loss.sum()


def zero_loss(device):
    return torch.tensor(0.0, device=device, requires_grad=True)

# with no reduction
def smooth_l1_loss(x, y, sigma):
    assert x.shape == y.shape
    abs_diff = torch.abs(x - y)
    sigma_sqr = sigma**2
    mask = abs_diff < 1 / (sigma_sqr)
    val = mask.float() * (sigma_sqr / 2.0) * (abs_diff**2) + \
          (1 - mask.float()) * (abs_diff - 0.5/sigma_sqr)
    return val.sum()

def smooth_l1_loss_v2(x, y, beta):
    assert beta > 0
    assert x.shape == y.shape
    abs_diff = torch.abs(x-y)
    mask = abs_diff < beta
    val = mask.float() * (abs_diff**2) / (2*beta) + (1-mask.float()) * (abs_diff - 0.5 * beta)
    return val.sum()

def normalize_weight(filt_weight, bias):
    assert len(filt_weight) > 0
    device = filt_weight[0].device
    wloss = []
    for w in filt_weight:
        w = w.view(w.shape[0], -1)
        num_w = w.shape[0]
        wnorm = torch.norm(w, dim=1)
        abs_diff = torch.abs(wnorm-1).sum()
        wloss.append(abs_diff/num_w)
    bloss = []
    for b in bias:
        bnorm = torch.norm(b)
        num_b = b.shape[0]
        bloss.append(torch.abs(bnorm-1)/num_b)
    return sum(wloss), sum(bloss)

class FocalLoss(nn.Module):
    def __init__(self, alpha=0.25, gamma=2.0, use_sigmoid=True, loss_weight=1.0):
        assert use_sigmoid == True, 'FocalLoss for non sigmoid is not implemented'
        super(FocalLoss, self).__init__()
        self.use_sigmoid=True
        self.alpha=0.25
        self.gamma=2.0
        self.loss_weight=1.0

    def forward(self, pred, target):
        '''
        Args:
            pred: [n, 20] where 20 is number of class channels
            target: [n] it contains n labels
        '''
        return self.loss_weight * sigmoid_focal_loss(pred, target, self.alpha, self.gamma)

class SmoothL1Loss(nn.Module):
    def __init__(self, beta=1.0, loss_weight=1.0):
        super(SmoothL1Loss, self).__init__()
        self.beta=beta
        self.loss_weight=loss_weight

    def forward(self, x, y):
        return self.loss_weight * smooth_l1_loss_v2(x, y, self.beta)


class CrossEntropyLoss(nn.Module):
    def __init__(self,
                 use_sigmoid=False,
                 loss_weight=1.0):
        super(CrossEntropyLoss, self).__init__()
        self.use_sigmoid=use_sigmoid
        self.loss_weight=loss_weight

    def forward(self, pred, label):
        '''
        Args:
            pred: [n, 21] where 21 is number of class channels, it usually contain backgroud channel
            target: [n] it contains n labels
        '''
        n_classes = pred.shape[1]
        if self.use_sigmoid:
            if n_classes == 1:
                loss = F.binary_cross_entropy_with_logits(pred, label.view(-1, 1).float(), reduction='none')
                return loss.sum() * self.loss_weight
            else:
                tar_one_hot = utils.one_hot_embedding(label, n_classes+1)
                tar_one_hot = tar_one_hot[:, 1:]
                tar_one_hot = tar_one_hot.to(dtype=pred.dtype)
                loss = F.binary_cross_entropy_with_logits(pred, tar_one_hot, reduction='none')
                return loss.sum() * self.loss_weight
        else:
            loss = F.cross_entropy(pred, label, reduction='none')
            return loss.sum() * self.loss_weight

def balanced_l1_loss(pred, target, beta=1.0, alpha=0.5, gamma=1.5):
    # borrowed from mmdet
    assert pred.size() == target.size()
    diff = torch.abs(pred-target)
    b = np.e**(gamma/alpha) - 1
    loss = torch.where(
        diff < beta,
        alpha / b * (b*diff + 1)*torch.log(b*diff/beta + 1) - alpha*diff,
        gamma*diff+gamma/b-alpha*beta
    )
    return loss

class BalancedL1Loss(nn.Module):
    def __init__(self, alpha=0.5, gamma=1.5, beta=1.0, loss_weight=1.0):
        super(BalancedL1Loss, self).__init__()
        self.alpha=alpha
        self.gamma=gamma
        self.beta=beta
        self.loss_weight=loss_weight

    def forward(self, pred, label):
        b_loss = balanced_l1_loss(pred, label, beta=self.beta, alpha=self.alpha, gamma=self.gamma)
        return b_loss.sum() * self.loss_weight

class BoundedIoULoss(nn.Module):
    def __init__(self, beta=0.2, loss_weight=1.0):
        self.beta=beta
        self.loss_weight=loss_weight
        super(BoundedIoULoss, self).__init__()

    def forward(self, x, y):
        assert x.shape == y.shape
        x = x + 1e-6
        y = y + 1e-6
        loss = 1-torch.min(x/y, y/x)
        return smooth_l1_loss_v2(loss, loss.new_zeros(loss.size()), self.beta).sum()


class GIoULoss(nn.Module):
    def __init__(self, loss_weight=1.0):
        super(GIoULoss, self).__init__()
        self.loss_weight = loss_weight

    def forward(self, a, b, weight=None, avg_factor=1.0):
        loss = giou_loss(a, b)
        if weight is not None:
            loss = loss * weight
        return loss.sum() * self.loss_weight / avg_factor

class IoULoss(nn.Module):
    def __init__(self, loss_weight=1.0):
        super(IoULoss, self).__init__()
        self.loss_weight = loss_weight

    def forward(self, a, b, weight=None, avg_factor=None):
        loss = iou_loss(a, b)
        if weight is not None:
            loss = loss * weight
        if avg_factor is not None:
            loss = loss / avg_factor
        return loss.sum() * self.loss_weight

# pred has the same shape as tar, and pred is logits
def generalized_focal_loss(pred, tar, beta=2.0):
    pred_sig = pred.sigmoid()
    focal_weight = (tar - pred_sig).abs().pow(beta)
    return focal_weight * F.binary_cross_entropy_with_logits(pred, tar, reduction='none')
    
class QualityFocalLoss(nn.Module):
    def __init__(self, beta=2.0, use_sigmoid=True, loss_weight=1.0):
        assert use_sigmoid, 'QualityFocalLoss only support sigmoid activation'
        self.use_sigmoid = use_sigmoid
        self.beta = beta
        self.loss_weight = loss_weight
        super(QualityFocalLoss, self).__init__()

    def forward(self, pred, quality, label, weight=None, avg_factor=1.0):
        '''
        pred: [n, n_cls], which is logits
        quality: [n], score of each label 
        label: [n], n labels
        '''
        n, n_cls = pred.shape
        tar = pred.new_full((n, n_cls+1), 0.0)
        tar[torch.arange(n), label] = quality
        tar = tar[:, 1:]
        loss = generalized_focal_loss(pred, tar, beta=self.beta)
        if weight is not None:
            loss = loss * weight
        return loss.sum() * self.loss_weight / avg_factor

# logits: [n, cls_channels]
# it applies a softmax followed by a log, it uses a stable implementation
def log_softmax_with_logits(logits):
    C, _ = logits.detach().max(1)
    logits_stable = logits - C.unsqueeze(1)
    return logits_stable - logits_stable.exp().sum(1).log().unsqueeze(1)
            
class DistributionFocalLoss(nn.Module):
    def __init__(self, cls_channels, stride, norm_prob, loss_weight=1.0):
        # TODO: the calc of loss does not use strides, it simply store the setting for detectors
        self.stride = stride
        self.cls_channels = cls_channels
        self.loss_weight = loss_weight
        self.norm_prob = norm_prob
        super(DistributionFocalLoss, self).__init__()        

    def forward(self, pred, y, left_idx, weight=None, avg_factor=1.0):
        '''
        pred:   [n, cls_channels], logits
        y: [n], target value
        left_idx: [n], left discrete bound next to y
        '''
        n, n_cls = pred.shape
        stride = self.stride
        log_sigma = log_softmax_with_logits(pred)
        right_idx = left_idx + 1
        y_left, y_right = left_idx * stride, right_idx * stride
        left_pred = pred[torch.arange(n), left_idx]
        right_pred = pred[torch.arange(n), right_idx]
        loss = (y_right - y) * log_sigma[torch.arange(n), left_idx] \
               + (y - y_left) * log_sigma[torch.arange(n), right_idx]
        if self.norm_prob:
            loss = loss / self.stride
        return -loss.sum() * self.loss_weight / avg_factor

