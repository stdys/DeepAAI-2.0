#!/usr/bin/env
# coding:utf-8

import torch
import torch.nn.functional as F
import torch.nn as nn
from models.layers.gcn_conv_input_mat import GCNConv

class ATT(nn.Module):
    def __init__(self, channels=2, r=2):
        super(ATT, self).__init__()
        inter_channels = int(channels // r)

        self.local_att = nn.Sequential(
            nn.Conv1d(channels, inter_channels, kernel_size=1, stride=1, padding=0),
        )

        self.local_att2 = nn.Sequential(
            nn.Conv1d(channels, inter_channels, kernel_size=1, stride=1, padding=0),
        )

        self.sigmoid = nn.Sigmoid()

    def forward(self, x, residual):
        xa = x + residual
        xl = self.local_att(xa)
        xlg = xl
        wei = self.sigmoid(xlg)
        xi = x * wei + residual * (1 - wei)

        xl2 = self.local_att2(xi)
        xlg2 = xl2
        wei2 = self.sigmoid(xlg2)
        xo = x * wei2 + residual * (1 - wei2)
        return xo


class CNNmodule(nn.Module):
    def __init__(self, in_channel, kernel_width, l=0):
        super(CNNmodule, self).__init__()
        self.kernel_width = kernel_width
        self.conv = nn.Conv1d(in_channels=in_channel, out_channels=64, kernel_size=2, stride=1, padding=1)
        self.pool = nn.MaxPool1d(kernel_size=2, stride=1)
        self.out_linear = nn.Linear(l*64, 512)
        self.dropout = nn.Dropout(0.5)


    def forward(self, protein_ft):
        '''
        :param protein_ft: batch*len*amino_dim
        :return:
        '''
        batch_size = protein_ft.size()[0]
        protein_ft = protein_ft.transpose(1, 2)

        conv_ft = self.conv(protein_ft)
        conv_ft = self.dropout(conv_ft)
        conv_ft = self.pool(conv_ft).view(batch_size, -1)
        conv_ft = self.out_linear(conv_ft)
        return conv_ft


class DeepAAIKmerEmbeddingCls(nn.Module):
    def __init__(self, **param_dict):
        super(DeepAAIKmerEmbeddingCls, self).__init__()
        self.amino_ft_dim = param_dict['amino_type_num'],
        self.param_dict = param_dict
        self.kmer_dim = param_dict['kmer_dim']
        self.h_dim = param_dict['h_dim']
        self.dropout = param_dict['dropout_num']
        self.add_bn = param_dict['add_bn']
        self.add_res = param_dict['add_res']
        self.amino_embedding_dim = param_dict['amino_embedding_dim']
        # self.kernel_cfg = param_dict['kernel_cfg']
        # self.channel_cfg = param_dict['channel_cfg']
        # self.dilation_cfg = param_dict['dilation_cfg']

        self.antibody_kmer_linear = nn.Linear(param_dict['kmer_dim'], self.h_dim)
        self.virus_kmer_linear = nn.Linear(param_dict['kmer_dim'], self.h_dim)

#         self.antibody_pssm_linear = nn.Linear(param_dict['pssm_antibody_dim'], self.h_dim)
#         self.virus_pssm_linear = nn.Linear(param_dict['pssm_virus_dim'], self.h_dim)

        self.share_linear = nn.Linear(self.h_dim, self.h_dim)
        self.share_gcn1 = GCNConv(self.h_dim, self.h_dim)
        self.share_gcn2 = GCNConv(self.h_dim, self.h_dim)

        self.antibody_adj_trans = nn.Linear(self.h_dim, self.h_dim)
        self.virus_adj_trans = nn.Linear(self.h_dim, self.h_dim)

        self.cross_scale_merge = nn.Parameter(
            torch.ones(1)
        )

        # self.amino_embedding_layer = nn.Embedding(param_dict['amino_type_num'], self.amino_embedding_dim)
        # self.channel_cfg.insert(0, self.amino_embedding_dim)
        # self.local_linear = nn.Linear(self.channel_cfg[-1] * 2, self.h_dim)
        self.global_linear = nn.Linear(self.h_dim * 2, self.h_dim)
        self.pred_linear = nn.Linear(self.h_dim, 1)

        self.activation = nn.ELU()
        for m in self.modules():
            self.weights_init(m)
            
        self.max_antibody_len = param_dict['max_antibody_len']
        self.max_virus_len =  param_dict['max_virus_len']
            
        self.cnnmodule = CNNmodule(in_channel=22, kernel_width=self.amino_ft_dim, l=self.max_antibody_len)
        self.cnnmodule2 = CNNmodule(in_channel=22, kernel_width=self.amino_ft_dim, l=self.max_virus_len)

        self.out_linear = nn.Linear(1024, 512)

        self.att = ATT(channels=2, r=1)

    def weights_init(self, m):
        if isinstance(m, nn.Linear):
            torch.nn.init.xavier_uniform_(m.weight.data)
            m.bias.data.fill_(0.0)


    def forward(self, **ft_dict):
        '''
        :param ft_dict:
                ft_dict = {
                'antibody_graph_node_ft': FloatTensor  node_num * kmer_dim
                'virus_graph_node_ft': FloatTensor  node_num * kmer_dim,
                'antibody_amino_ft': LongTensor  batch * max_antibody_len * 1
                'virus_amino_ft': LongTensor  batch * max_virus_len * 1,
                'antibody_idx': LongTensor  batch
                'virus_idx': LongTensor  batch
            }
        :return:
        '''
        device = ft_dict['antibody_graph_node_kmer_ft'].device
        antibody_graph_node_num = ft_dict['antibody_graph_node_kmer_ft'].size()[0]
        virus_graph_node_num = ft_dict['virus_graph_node_kmer_ft'].size()[0]
        antibody_res_mat = torch.zeros(antibody_graph_node_num, self.h_dim).to(device)
        virus_res_mat = torch.zeros(virus_graph_node_num, self.h_dim).to(device)

        antibody_node_kmer_ft = self.antibody_kmer_linear(ft_dict['antibody_graph_node_kmer_ft'])
        antibody_node_ft = antibody_node_kmer_ft
        antibody_node_ft = self.activation(antibody_node_ft)
        antibody_node_ft = F.dropout(antibody_node_ft, p=self.dropout, training=self.training)

        # share
        antibody_node_ft = self.share_linear(antibody_node_ft)
        antibody_res_mat = antibody_res_mat + antibody_node_ft
        antibody_node_ft = self.activation(antibody_node_ft)
        antibody_node_ft = F.dropout(antibody_node_ft, p=self.dropout, training=self.training)

        # generate antibody adj
        antibody_trans_ft = self.antibody_adj_trans(antibody_node_ft)
        antibody_trans_ft = torch.tanh(antibody_trans_ft)
        w = torch.norm(antibody_trans_ft, p=2, dim=-1).view(-1, 1)
        w_mat = w * w.t()
        antibody_adj = torch.mm(antibody_trans_ft, antibody_trans_ft.t()) / w_mat

        antibody_node_ft = self.share_gcn1(antibody_node_ft, antibody_adj)
        antibody_res_mat = antibody_res_mat + antibody_node_ft

        antibody_node_ft = self.activation(antibody_res_mat)  # add
        antibody_node_ft = F.dropout(antibody_node_ft, p=self.dropout, training=self.training)
        antibody_node_ft = self.share_gcn2(antibody_node_ft, antibody_adj)
        antibody_res_mat = antibody_res_mat + antibody_node_ft

        # virus
        virus_node_kmer_ft = self.virus_kmer_linear(ft_dict['virus_graph_node_kmer_ft'])
        virus_node_ft = virus_node_kmer_ft
        virus_node_ft = self.activation(virus_node_ft)
        virus_node_ft = F.dropout(virus_node_ft, p=self.dropout, training=self.training)

        # share
        virus_node_ft = self.share_linear(virus_node_ft)
        virus_res_mat = virus_res_mat + virus_node_ft
        virus_node_ft = self.activation(virus_node_ft)
        virus_node_ft = F.dropout(virus_node_ft, p=self.dropout, training=self.training)

        # generate antibody adj
        virus_trans_ft = self.virus_adj_trans(virus_node_ft)
        virus_trans_ft = torch.tanh(virus_trans_ft)
        w = torch.norm(virus_trans_ft, p=2, dim=-1).view(-1, 1)
        w_mat = w * w.t()
        virus_adj = torch.mm(virus_trans_ft, virus_trans_ft.t()) / w_mat
        # virus_adj = eye_adj

        virus_node_ft = self.share_gcn1(virus_node_ft, virus_adj)
        virus_res_mat = virus_res_mat + virus_node_ft

        virus_node_ft = self.activation(virus_res_mat)  # add
        virus_node_ft = F.dropout(virus_node_ft, p=self.dropout, training=self.training)
        virus_node_ft = self.share_gcn2(virus_node_ft, virus_adj)
        virus_res_mat = virus_res_mat + virus_node_ft

        antibody_res_mat = self.activation(antibody_res_mat)
        virus_res_mat = self.activation(virus_res_mat)

        antibody_res_mat = antibody_res_mat[ft_dict['antibody_idx']]
        virus_res_mat = virus_res_mat[ft_dict['virus_idx']]

        batch_size = ft_dict['virus_amino_ft'].size()[0]
        antibody_ft = self.cnnmodule(ft_dict['antibody_amino_ft']).view(batch_size, -1)
        virus_ft = self.cnnmodule2(ft_dict['virus_amino_ft']).view(batch_size, -1)

        global_ft = torch.cat([virus_res_mat.unsqueeze(1), antibody_res_mat.unsqueeze(1)], dim=-2)
        local_ft = torch.cat([virus_ft.unsqueeze(1), antibody_ft.unsqueeze(1)], dim=-2)
        
        pair_ft = self.att(global_ft, local_ft)

        pair_ft = pair_ft.view(batch_size, -1)
        pair_ft = self.out_linear(pair_ft)

        pred = self.pred_linear(pair_ft)
        pred = torch.sigmoid(pred)

        return pred, antibody_adj, virus_adj