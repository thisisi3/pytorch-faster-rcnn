from .hooks import OptimizerHook, Hookable, LrHook, CkptHook, ReportHook
from collections import OrderedDict
import torch, time, copy, logging, traceback
import os.path as osp


class BasicTrainer(Hookable):
    def __init__(self,
                 dataloader,
                 work_dir,
                 model,
                 train_cfg,
                 optimizer_cfg,
                 optim_cfg,
                 lr_cfg,
                 ckpt_cfg,
                 report_cfg,
                 log_cfg=None,
                 device='cpu'):
        super(BasicTrainer, self).__init__()
        self.device=device
        self.dataloader=dataloader
        self.work_dir=work_dir
        self.model=model
        self.train_cfg=train_cfg
        self.optimizer_cfg=optimizer_cfg
        self.optim_cfg=optim_cfg
        self.lr_cfg=lr_cfg
        self.log_cfg=log_cfg
        self.ckpt_cfg=ckpt_cfg
        self.report_cfg=report_cfg

        self.cur_iter=1
        self.cur_epoch=1
        self.total_epochs=train_cfg.total_epochs
        self.initial_lr=optimizer_cfg.lr
        
        self.init_optimizer()
        self.cur_loss=None

        self.add_hook(OptimizerHook(self))
        self.add_hook(LrHook(self))
        self.add_hook(CkptHook(self))
        self.add_hook(ReportHook(self))

    def init_optimizer(self):
        from ..builder import build_module
        optimizer_cfg = copy.deepcopy(self.optimizer_cfg)
        self.optimizer = build_module(optimizer_cfg, params=self.model.parameters())
        logging.info('Created optimizer with cfg: {}'.format(self.optimizer_cfg))

    def init_detector(self):
        self.model.init_weights()

    def get_lr(self):
        return [pg['lr'] for pg in self.optimizer.param_groups]

    def set_lr(self, lr):
        if isinstance(lr, float):
            for pg in self.optimizer.param_groups:
                pg['lr'] = lr
        elif isinstance(lr, list):
            for i, pg in enumerate(self.optimizer.param_groups):
                pg['lr'] = lr[i]
        else:
            raise ValueError('lr must be either a float or a list of float numbers.')

    # current loss maybe none if after start of current iter and before forward_train()
    def get_loss(self):
        return self.cur_loss

    def get_total_iters(self):
        return len(self.dataloader) * (self.total_epochs)

    def train(self):
        logging.info('Start a new training, start with epoch {}'.format(self.cur_epoch))
        self.model.to(self.device)
        self.model.train()
        dataset_size = len(self.dataloader)
        logging.info('Dataset size: {}'.format(dataset_size))
        self.call_hooks('before_train_all')
        logging.info('Initial lr: {}'.format(self.initial_lr))
        for epoch in range(self.cur_epoch, self.total_epochs+1):
            self.cur_epoch = epoch
            self.call_hooks('before_epoch')
            logging.info('Start to train epoch={}, with lr={}'.format(epoch, self.get_lr()))
            for iter_i, train_data in enumerate(self.dataloader):
                try:
                    self.train_one_iter(iter_i, epoch, train_data)
                    self.cur_iter += 1
                except:
                    logging.error('Traceback: {}'.format(traceback.format_exc()))
                    print(traceback.format_exc())
                    print('Training is interrupted by an error, :(...')
                    exit()
            logging.info('Finished train of epoch={}'.format(epoch))
            self.call_hooks('after_epoch')
        self.call_hooks('after_train_all')

    def train_one_iter(self, iter_i, epoch, train_data):
        self.call_hooks('before_iter')
        img_metas = train_data['img_meta'].data[0]
        img_data = train_data['img'].data[0].to(self.device)

        gt_bboxes = train_data['gt_bboxes'].data[0]
        gt_bboxes = [gt_bbox.to(self.device).t() for gt_bbox in gt_bboxes]
        gt_labels = train_data['gt_labels'].data[0]
        gt_labels = [gt_label.to(self.device) for gt_label in gt_labels]

        logging.info('\n'+' At epoch {}, iteration {} '.center(100, '#').format(epoch, iter_i))
        logging.info('Image data: {}'.format(img_data.shape))
        logging.info('Image metas: {}'.format('\n'.join([str(img_meta) for img_meta in img_metas])))
        logging.info('GT Bbox: {}'.format(', '.join([str(gt_bbox.shape) for gt_bbox in gt_bboxes])))
        self.optimizer.zero_grad()
        losses = self.model.forward_train(img_data, gt_bboxes, gt_labels, img_metas)
        self.cur_loss = OrderedDict({k:v.item() for k, v in losses.items()})

        for loss_name, loss_val in losses.items():
            logging.info('{}: {}'.format(loss_name, loss_val.item()))
        tot_loss = sum([loss_val for _, loss_val in losses.items()])
        logging.info('{}: {}'.format('tot_loss', tot_loss.item()))
        tot_loss.backward()
        self.call_hooks('after_iter')
        
        self.call_hooks('before_step')
        self.optimizer.step()
        self.call_hooks('after_step')

        
