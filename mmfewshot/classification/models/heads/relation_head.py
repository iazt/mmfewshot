import math

import torch
import torch.nn as nn
import torch.nn.functional as F
from mmcls.models.builder import HEADS

from mmfewshot.classification.datasets.utils import label_wrapper
from .base_head import FewShotBaseHead


@HEADS.register_module()
class RelationHead(FewShotBaseHead):
    """Classification head for `RelationNet.

    <https://arxiv.org/abs/1711.06025>`_.

    Args:
        in_channels (int): Number of channels in the input feature map.
        feature_size (tuple(int, int)): Size of the input feature map.
            Default: (7, 7).
        hidden_channels (int): Number of channels for the hidden fc layer.
            Default: 8.
        loss (dict): Training loss. Options are CrossEntropyLoss and MSELoss.
    """

    def __init__(self,
                 in_channels,
                 feature_size=(7, 7),
                 hidden_channels=8,
                 loss=dict(type='CrossEntropyLoss', loss_weight=1.0),
                 *args,
                 **kwargs):
        super(RelationHead, self).__init__(loss=loss, *args, **kwargs)

        self.in_channels = in_channels
        self.feature_size = feature_size
        self.hidden_channels = hidden_channels
        self.padding = 1 if (feature_size[0] < 10) and (
            feature_size[1] < 10) else 0
        self.loss_type = loss['type']
        self.init_layer()
        self.init_weights()

        self.support_feats = []
        self.support_labels = []
        self.prototype_feats = None

    def init_layer(self):
        self.layer1 = nn.Sequential(
            nn.Conv2d(
                self.in_channels * 2,
                self.in_channels,
                3,
                padding=self.padding),
            nn.BatchNorm2d(self.in_channels, momentum=1, affine=True),
            nn.ReLU(), nn.MaxPool2d(2))
        self.layer2 = nn.Sequential(
            nn.Conv2d(
                self.in_channels, self.in_channels, 3, padding=self.padding),
            nn.BatchNorm2d(self.in_channels, momentum=1, affine=True),
            nn.ReLU(), nn.MaxPool2d(2))

        def shrink_fn(s):
            return int((int(
                (s - 2 + 2 * self.padding) / 2) - 2 + 2 * self.padding) / 2)

        self.fc1 = nn.Linear(
            self.in_channels * shrink_fn(self.feature_size[0]) *
            shrink_fn(self.feature_size[1]), self.hidden_channels)
        self.fc2 = nn.Linear(self.hidden_channels, 1)
        self.relu = nn.ReLU(inplace=True)

    def init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                m.weight.data.normal_(0, math.sqrt(2.0 / float(n)))
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.fill_(0)
            elif isinstance(m, nn.Linear):
                m.weight.data.normal_(0, 0.01)
                m.bias.data.fill_(1)

    def forward_relation_module(self, x):
        """Forward function for relation module."""
        out = self.layer1(x)
        out = self.layer2(out)
        out = out.view(out.size(0), -1)
        out = self.relu(self.fc1(out))
        out = self.fc2(out)
        return out

    def forward_train(self, support_feats, support_labels, query_feats,
                      query_labels, **kwargs):
        """Forward training data.

        Args:
            support_feats (Tensor): Features of support data with shape
                (N, C, H, W).
            support_labels (Tensor): Labels of support data with shape (N).
            query_feats (Tensor): Features of query data with shape
                (N, C, H, W).
            query_labels (Tensor): Labels of query data with shape (N).

        Returns:
            dict[str, Tensor]: A dictionary of loss components.
        """
        class_ids = torch.unique(support_labels)
        prototype_feats = [
            support_feats[support_labels == class_id].mean(0, keepdim=True)
            for class_id in class_ids
        ]
        prototype_feats = torch.cat(prototype_feats, dim=0)
        num_query, c, h, w = query_feats.size()
        num_way, c, h, w = prototype_feats.size()
        # shape (n_query * n_way, c, h, W)
        repeated_prototype_feats = prototype_feats.unsqueeze(0).repeat(
            num_query, 1, 1, 1, 1).view(num_query * num_way, c, h, w)
        # shape (n_query * n_way, c, h, W)
        repeated_query_feats = query_feats.unsqueeze(1).repeat(
            1, num_way, 1, 1, 1).view(num_query * num_way, c, h, w)
        # shape (n_way * n_query, 2 * c, h, W)
        pair_feats = torch.cat(
            (repeated_query_feats, repeated_prototype_feats), 1)
        cls_score = self.forward_relation_module(pair_feats)

        if self.loss_type == 'MSELoss':
            cls_score = torch.sigmoid(cls_score)
            repeated_prototype_labels = class_ids.unsqueeze(0).repeat(
                num_query, 1).view(-1)
            repeated_query_labels = query_labels.unsqueeze(1).repeat(
                1, num_way).view(-1)
            pair_labels = (repeated_query_labels == repeated_prototype_labels
                           ).float().unsqueeze(1)
            losses = self.loss(cls_score, pair_labels)
        elif self.loss_type == 'CrossEntropyLoss':
            labels = label_wrapper(query_labels, class_ids.cpu().tolist())
            cls_score = cls_score.view(num_query, num_way)
            losses = self.loss(cls_score, labels)
        else:
            raise TypeError('only support CrossEntropyLoss and MSELoss')

        return losses

    def forward_support(self, x, gt_label, **kwargs):
        """Forward support data in meta testing."""
        self.support_feats.append(x)
        self.support_labels.append(gt_label)

    def forward_query(self, x, **kwargs):
        """Forward query data in meta testing."""
        assert self.prototype_feats is not None
        num_way, c, h, w = self.prototype_feats.size()
        num_query, c, h, w = x.size()
        # shape (n_query * n_way, c, h, W)
        repeated_prototype_feats = self.prototype_feats.unsqueeze(0).repeat(
            num_query, 1, 1, 1, 1).view(num_query * num_way, c, h, w)
        # shape (n_query * n_way, c, h, W)
        repeated_query_feats = x.unsqueeze(1).repeat(1, num_way, 1, 1, 1).view(
            num_query * num_way, c, h, w)
        # shape (n_way * n_query, 2 * c, h, W)
        pair_feats = torch.cat(
            (repeated_query_feats, repeated_prototype_feats), 1)
        cls_score = self.forward_relation_module(pair_feats).view(
            num_query, num_way)
        pred = F.softmax(cls_score, dim=1)
        pred = list(pred.detach().cpu().numpy())
        return pred

    def before_forward_support(self):
        """Used in meta testing.

        This function will be called before model forward support data during
        meta testing.
        """
        # reset prototype features for testing new task
        self.support_feats.clear()
        self.support_labels.clear()
        self.prototype_feats = None

    def before_forward_query(self):
        """Used in meta testing.

        This function will be called before model forward query data during
        meta testing.
        """
        feats = torch.cat(self.support_feats, dim=0)
        labels = torch.cat(self.support_labels, dim=0)
        class_ids, _ = torch.unique(labels).sort()
        prototype_feats = [
            feats[labels == class_id].mean(0, keepdim=True)
            for class_id in class_ids
        ]
        self.prototype_feats = torch.cat(prototype_feats, dim=0)
