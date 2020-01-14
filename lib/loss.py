import torch.nn as nn
import torch, logging
from . import region

def zero_loss(device):
    return torch.tensor(0.0, device=device, requires_grad=True)

def smooth_l1_loss(x, y, sigma):
    assert x.shape == y.shape
    abs_diff = torch.abs(x - y)
    sigma_sqr = sigma**2
    mask = abs_diff < 1 / (sigma_sqr)
    val = mask.float() * (sigma_sqr / 2.0) * (abs_diff**2) + \
          (1 - mask.float()) * (abs_diff - 0.5/sigma_sqr)
    return val.sum()
    

class RPNLoss(object):
    def __init__(self, lamb=1.0, sigma=3.0):
        self.lamb = lamb
        self.sigma = sigma
        self.ce = nn.CrossEntropyLoss()
        self.smooth_l1 = nn.SmoothL1Loss(reduction='sum')

    def __call__(self, cls_out, reg_out, label, param):
        if label.numel() == 0:
            logging.warning('RPN receives no samples to train.')
            return zero_loss(cls_out.device)
        cls_loss = self.ce(cls_out.t(), label.long())
        n_samples = len(label)
        pos_arg = (label==1)
        if pos_arg.sum() == 0:
            logging.warning('RPN receives no positive samples.')
            reg_loss = zero_loss(cls_out.device)
        else:
            # reg_loss = self.smooth_l1(reg_out[:, pos_arg], param[:, pos_arg]) / n_samples
            reg_loss = smooth_l1_loss(reg_out[:, pos_arg], param[:, pos_arg],
                                      self.sigma) / n_samples
        return cls_loss + self.lamb * reg_loss

class RCNNLoss(object):
    def __init__(self, lamb=1.0, sigma=1.0):
        self.lamb = lamb
        self.sigma = sigma
        self.ce = nn.CrossEntropyLoss()
        self.smooth_l1 = nn.SmoothL1Loss(reduction='sum')

    def __call__(self, cls_out, reg_out, label, param):
        if label.numel() == 0 or cls_out is None or reg_out is None:
            logging.warning('RCNN receives no training rois.')
            return zero_loss(label.device)
        label = label.long()
        n_class = cls_out.shape[1]
        n_samples = len(label)
        cls_loss = self.ce(cls_out, label)
        reg_out = reg_out.view(-1, 4, n_class)
        reg_out = reg_out[torch.arange(n_samples), :, label]
        pos_arg = (label>=1)
        if pos_arg.sum() == 0:
            logging.warning('RCNN recieves no positive samples.')
            reg_loss = zero_loss(label.device)
        else:
            pos_reg = reg_out[pos_arg, :]
            # reg_loss = self.smooth_l1(pos_reg, param[:, pos_arg].t()) / n_samples
            reg_loss = smooth_l1_loss(pos_reg, param[:, pos_arg].t(),
                                      self.sigma) / n_samples
        return cls_loss + self.lamb * reg_loss

