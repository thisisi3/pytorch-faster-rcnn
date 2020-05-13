from . import utils
import torch

def tensor_shape(tsr):
    print(utils.tensor_shape(tsr))

def count_tensor(tsr):
    print(utils.count_tensor(tsr))

def peek_tensor(tsr):
    assert isinstance(tsr, torch.Tensor)
    out_str = 'size:{}, dtype:{}, device:{}'\
              .format(tsr.shape, tsr.dtype, tsr.device)
    print(out_str)
