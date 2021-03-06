from PIL import Image

import torchvision as tv
import torch
import numpy as np
from . import debug

IMGNET_MEAN = [0.485, 0.456, 0.406]
IMGNET_STD  = [0.229, 0.224, 0.225]

# transform from PIL image to normalized tensor with shape [3 x H x W]
IMG_PIL2TENSOR = tv.transforms.Compose(
    [tv.transforms.ToTensor(),
     tv.transforms.Normalize(mean=IMGNET_MEAN, std=IMGNET_STD)])

def sum_list(lst):
    assert len(lst) > 0
    res = lst[0]
    for i in range(1, len(lst)):
        res += lst[i]
    return res


def input_size(img_metas):
    pad_sizes = [img_meta['pad_shape'][:2] for img_meta in img_metas]
    return [max([pad_size[i] for pad_size in pad_sizes]) for i in range(2)]

def dict2str(d):
    return '{ '+', '.join(['{}:{}'.format(k, dict2str(v)) for k,v in d.items()])+' }' \
        if isinstance(d, dict) else str(d)

def image2feature(bbox, img_size, feat_size):                                                                             
    h_rat, w_rat = [feat_size[i]/img_size[i] for i in range(2)]
    return bbox * torch.tensor([[w_rat], [h_rat], [w_rat], [h_rat]],
                               device=bbox.device, dtype=torch.float32)

def index_of(bool_tsr):
    return tuple(torch.nonzero(bool_tsr).t())

def wh_from_xyxy(bbox):
    w,h = bbox[2] - bbox[0] + 1, bbox[3] - bbox[1] + 1
    return w,h

def center_of(bbox):
    return (bbox[2]+bbox[0])/2, (bbox[3]+bbox[1])/2

def bbox2param(base, bbox, means=[0.0, 0.0, 0.0, 0.0], stds=[1.0, 1.0, 1.0, 1.0]):
    """
    It calculates the relative distance of a bbox to a base bbox.

    Args:
        base (tensor): the base bbox, of size (4, n) where n is number of base bboxes
              and 4 is (x_min, y_min, x_max, y_max) or (left, top, right, bottom)
        bbox (tensor): the bbox 
    Returns:
        parameters representing the distance of bbox to base bbox (tx, ty, tw, th)
    """
    assert base.shape == bbox.shape
    
    base_w, base_h = wh_from_xyxy(base)
    bbox_w, bbox_h = wh_from_xyxy(bbox)
    base_center = center_of(base)
    bbox_center = center_of(bbox)
    tx, ty = (bbox_center[0]-base_center[0])/base_w, (bbox_center[1]-base_center[1])/base_h
    #tx, ty = (bbox[0]-base[0])/base_w, (bbox[1]-base[1])/base_h
    tw, th = torch.log(bbox_w/base_w), torch.log(bbox_h/base_h)
    param = torch.stack([tx, ty, tw, th])
    means = base.new(means).view(4, -1)
    stds = base.new(stds).view(4, -1)
    return (param - means) / stds

"""
It applies delta to base bboxes.

Args:
    base [4, n]: the base bbox
    param [4, n]: delta 
    means and stds: this means params are normalized
    img_size: if this is present, need to clamp size of bboxes
Returns:
    the original bbox
"""
def param2bbox(base, param, means=[0.0, 0.0, 0.0, 0.0], stds=[1.0, 1.0, 1.0, 1.0], img_size=None):
    assert base.shape == param.shape
    assert base.shape[0] == 4
    means= base.new(means).view(4, -1)
    stds = base.new(stds).view(4, -1)
    param = param * stds + means
    bbox = _param2bbox_(base, param)
    if img_size is not None:
        bbox = clamp_bbox(bbox, img_size)
    return bbox

# param [4, n] or [4*num_cls, n]
# return bbox[4*num_cls, n]
def batched_param2bbox(base, param,
                       means=[0.0, 0.0, 0.0, 0.0], stds=[1.0, 1.0, 1.0, 1.0], img_size=None):
    assert param.shape[0] % 4 == 0
    batch_size = param.shape[0] // 4
    if batch_size == 1:
        return param2bbox(base, param, means, stds, img_size)
    param = param.view(4, batch_size, -1)
    bboxes = []
    for i in range(batch_size):
        bboxes.append(param2bbox(base, param[:, i, :], means, stds, img_size))
    return torch.stack(bboxes, dim=1).view(4*batch_size, -1)
    

def clamp_bbox(bbox, img_size):
    '''
    Args: 
        bbox: [4, n], bboxes in xyxy order
        img_size: [h, w], image size
    '''
    H, W = img_size[:2]
    return torch.stack([
        bbox[0].clamp(0.0, W-1),
        bbox[1].clamp(0.0, H-1),
        bbox[2].clamp(0.0, W-1),
        bbox[3].clamp(0.0, H-1)])

def scale_wrt_center(bbox, scale):
    assert scale > 0
    ctr_x, ctr_y = center_of(bbox)
    w, h = wh_from_xyxy(bbox)
    w, h = w * scale, h * scale
    dist_w, dist_h = w/2, h/2
    return torch.stack([
        ctr_x - dist_w,
        ctr_y - dist_h,
        ctr_x + dist_w,
        ctr_y + dist_h])

def _param2bbox_(base, param):
    assert base.shape == param.shape
    base_w, base_h = wh_from_xyxy(base)
    base_center_x, base_center_y = center_of(base)
    tx, ty, tw, th = [param[i] for i in range(4)]
    center_x, center_y = tx*base_w + base_center_x, ty*base_h + base_center_y
    w, h = torch.exp(tw)*base_w, torch.exp(th)*base_h
    return torch.stack([center_x-w/2,
                        center_y-h/2,
                        center_x+w/2,
                        center_y+h/2])

def xyxy2xywh(xyxy):
    return torch.stack([xyxy[0], xyxy[1], xyxy[2]-xyxy[0]+1, xyxy[3]-xyxy[1]+1])
def xywh2xyxy(xywh):
    return torch.stack([xywh[0], xywh[1], xywh[0]+xywh[2]-1, xywh[1]+xywh[3]-1])
    
def calc_iou(a, b):
    """
    It calculates iou of a_i and b_j and put it in a table of size (N, K). N is number of bbox
    in a and K is number of bbox in b.

    Args:
        a (tensor): an array of bboxes, it's size is (4, N), 
                    4 coordinates: (x_min, y_min, x_max, y_max)
        b (tensor): another array of bboxes, it's size is (4, K)

    Returns:
        tensor: a table if ious

    """
    assert a.shape[0] == 4 and b.shape[0] == 4
    tl = torch.max(a[:2].view(2, -1, 1), b[:2].view(2, 1, -1))
    br = torch.min(a[2:].view(2, -1, 1), b[2:].view(2, 1, -1))
    area_i = torch.prod(br - tl + 1, dim=0)
    area_i = area_i * (tl < br).all(dim=0).float()
    area_a = torch.prod(a[2:]-a[:2] + 1, dim=0)
    area_b = torch.prod(b[2:]-b[:2] + 1, dim=0)
    return area_i / (area_a.view(-1, 1) + area_b.view(1, -1) - area_i)

def elem_iou(a, b):
    assert a.shape[0]==4 and b.shape[0]==4 and a.shape == b.shape
    tl = torch.max(a[:2], b[:2])
    br = torch.min(a[2:], b[2:])
    area_i = torch.prod(br-tl, dim=0)
    area_i = area_i * (tl<br).all(0).float()
    area_a = torch.prod(a[2:]-a[:2], dim=0)
    area_b = torch.prod(b[2:]-b[:2], dim=0)
    return area_i / (area_a + area_b - area_i)
    

def init_module_normal(m, mean=0.0, std=1.0):
    for name, param in m.named_parameters():
        if 'weight' in name:
            param.data.normal_(mean, std)
        if 'bias' in name:
            param.data.zero_()

# label has -1:ignore, 0:negative, >0:specific positive index
# turn positive values into uniformly 1
def simplify_label(label):
    label_ = label.clone().detach()
    label_[label>0]=1
    return label_

def to_pair(val):
    if isinstance(val, int):
        pair=tuple([val, val])
    else:
        val = list(val)
        assert len(val) == 2
        pair = tuple(val)
    return pair


# apply torch nms in batched fashion
# bbox:[n, 4], score:[n], label:[n]
def batched_nms(bbox, score, label, nms_iou, class_agnostic=False):
    numel = score.numel()
    if numel == 0:
        return bbox, score, label
    if class_agnostic:
        nms_bbox = bbox
    else:
        max_range = bbox.max()
        nms_bbox = bbox + (label*max_range).to(bbox).view(numel, 1)
    keep = tv.ops.nms(nms_bbox, score, nms_iou)
    return bbox[keep, :], score[keep], label[keep]

# bbox[n, 4] or [n, 4*cls_channel], score: [n] or [n, cls_channel]
def multiclass_nms(bbox, score, nms_channel, nms_iou, min_score=-1,
                   max_num=None, score_factor=None, mode='official'):
    assert mode in ['official', 'strict']
    assert score.dim() == 2, 'multiclass_nms only applies to multi-channel score'
    cls_channel = score.shape[1]
    num_bbox = bbox.shape[0]
    simple_bbox = bbox.shape[1] == 4
    if mode == 'official':
        label = torch.full_like(score, -1).to(torch.long)
        for cha in nms_channel:
            label[:, cha] = cha
        chosen = (label!=-1)
        if simple_bbox:
            bbox = bbox.unsqueeze(2).expand(-1, -1, cls_channel)
        else:
            bbox = bbox.view(num_bbox, 4, cls_channel)
        bbox = bbox.permute(0, 2, 1)
        chosen = (score >= min_score) & chosen
        if score_factor is not None:
            if score_factor.dim()==1:
                score_factor = score_factor.unsqueeze(1)
            score = score * score_factor
        nms_bbox = bbox[chosen]
        nms_score = score[chosen]
        nms_label = label[chosen]
    else:
        # in strict mode, one bbox has only one label and participate in one nms
        score, label = score.max(1)
        chosen = (label < -1)
        for cha in nms_channel:
            chosen = chosen | (label==cha)
        if not simple_bbox:
            bbox = bbox.view(num_bbox, 4, cls_channel)[torch.arange(num_bbox), :, label] # [n, 4]
        chosen = (score >= min_score) & chosen
        if score_factor is not None:
            score = score * score_factor
        nms_bbox = bbox[chosen, :]
        nms_score = score[chosen]
        nms_label = label[chosen]
    
    keep_bbox, keep_score, keep_label = batched_nms(nms_bbox, nms_score, nms_label, nms_iou)
    if max_num is not None and keep_score.numel() > max_num:
        keep_bbox = keep_bbox[:max_num]
        keep_score = keep_score[:max_num]
        keep_label = keep_label[:max_num]
    return keep_bbox, keep_score, keep_label
        

def one_hot_embedding(label, n_cls):
    n = len(label)
    one_hot = label.new_full((n, n_cls), 0)
    one_hot[torch.arange(n), label] = 1
    return one_hot

def multi_apply(func, *args):
    list_args = [arg for arg in args if isinstance(arg, list)]
    if len(list_args) == 0:
        mult_arg_len = 1
    else:
        mult_arg_len = len(list_args[0])
    for arg in list_args:
        if len(arg) != mult_arg_len:
            raise ValueError('Arg: {} does not have the same length as others'.format(arg))
    result = []
    for i in range(mult_arg_len):
        cur_args = []
        for arg in args:
            if isinstance(arg, list):
                cur_args.append(arg[i])
            else:
                cur_args.append(arg)
        result.append(func(*cur_args))
    return result
# WARNING: assume the return of one call has multi values to avoid ambiguity 
def unpack_multi_result(multi_res):
    assert len(multi_res) != 0
    num_ret = len(multi_res[0])
    return [[res[i] for res in multi_res] for i in range(num_ret)]
        
def class_name(slf):
    return slf.__class__.__name__

def random_select_dim(tsr, dim, num, replace=False):
    tot = tsr.shape[dim]
    idx = np.random.choice(tot, num, replace)
    return tsr.index_select(dim, torch.tensor(idx, device=tsr.device))

def random_select(tsr, num, replace=False):
    return random_select_dim(tsr, 0, num, replace)

def count_tensor(tsr):
    assert isinstance(tsr, torch.Tensor)
    if tsr.dtype == torch.bool:
        tsr = tsr.int()
    uniq = tsr.unique()
    ret = []
    for v in uniq:
        ret.append(str(v.item()) + ':' + str((tsr==v).sum().item()))
    return ', '.join(ret)

def tensor_shape_helper(tsr):
    if isinstance(tsr, (tuple, list)):
        ret = []
        for x in tsr:
            ret += tensor_shape_helper(x)
        return [str(type(tsr))] + ['  ' + y for y in ret]
    elif isinstance(tsr, torch.Tensor):
        return [str(tsr.shape)]
    else:
        raise ValueError('Encounter non-tensor variable: {}'.format(type(tsr)))

def tensor_shape(tsr):
    return '\n'.join(tensor_shape_helper(tsr))

def full_index(size):
    full_one = torch.full(size, 1)
    all_idx = full_one.nonzero()
    return all_idx.view(*size, len(size))

# apply func to all elements in nested list or tuple
def inplace_apply(nested, func):
    if isinstance(nested, (list, tuple)):
        for i in range(len(nested)):
            nested[i] = inplace_apply(nested[i], func)
        return nested
    else:
        return func(nested)
    
# an example of tsr_list: [tensor(2, 3, 256, 256), ...]
# return [[tensor(1, 3, 256, 256), ...], [tensor(1, 3, 256, 256), ...]]
def split_by_image(tsr_list):
    num_imgs = tsr_list[0].shape[0]
    res = []
    for i in range(num_imgs):
        res.append([x[i] for x in tsr_list])
    return res
    

def sort_bbox(bbox, labels=None, descending=False):
    w, h = wh_from_xyxy(bbox)
    area = w*h
    v, idx = area.sort(descending=descending)
    return bbox[:, idx], labels[idx] if labels is not None else None
    

# grid_res is a list of results in grid format, e.g tensor([136, 100, 4]) which could be a
# coordinate data of feature map (136, 100)
# last means the data dim is the last, otherwise it is at the first
def concate_grid_result(grid_res, last=True):
    grid_res = [x.view(-1, x.shape[-1]) if last else x.view(x.shape[0], -1) for x in grid_res]
    return torch.cat(grid_res, dim=0 if last else -1)

    
    
class TextWriter(object):
    def __init__(self, target):
        self.target = target

    def write(self, txt):
        open(self.target, 'a').write(str(txt))

    def write_line(self, txt):
        open(self.target, 'a').write(txt+'\n')

