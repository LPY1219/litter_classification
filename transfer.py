#-------------------------------------#
#       对数据集进行训练
#-------------------------------------#
import os
import time
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.backends.cudnn as cudnn
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.autograd import Variable
from torch.utils.data import DataLoader
from tqdm import tqdm
from datetime import datetime
from nets.yolo4 import YoloBody
from nets.yolo_training import Generator, YOLOLoss
from utils.dataloader import YoloDataset, yolo_dataset_collate


os.environ["KMP_DUPLICATE_LIB_OK"]="TRUE"
#---------------------------------------------------#
#   获得类和先验框
#---------------------------------------------------#
def get_classes(classes_path):
    '''loads the classes'''
    with open(classes_path) as f:
        class_names = f.readlines()
    class_names = [c.strip() for c in class_names]
    return class_names

def get_anchors(anchors_path):
    '''loads the anchors from a file'''
    with open(anchors_path) as f:
        anchors = f.readline()
    anchors = [float(x) for x in anchors.split(',')]
    return np.array(anchors).reshape([-1,3,2])[::-1,:,:]

def get_lr(optimizer):
    for param_group in optimizer.param_groups:
        return param_group['lr']

def fit_one_epoch(net,yolo_losses,epoch,epoch_size,epoch_size_val,gen,genval,Epoch,cuda):
    total_loss = 0
    val_loss = 0

    net.train()
    with tqdm(total=epoch_size,desc=f'Epoch {epoch + 1}/{Epoch}',postfix=dict,mininterval=0.3) as pbar:
        for iteration, batch in enumerate(gen):
            if iteration >= epoch_size:
                break
            images, targets = batch[0], batch[1]
            with torch.no_grad():
                if cuda:
                    images = Variable(torch.from_numpy(images).type(torch.FloatTensor)).cuda()
                    targets = [Variable(torch.from_numpy(ann).type(torch.FloatTensor)) for ann in targets]
                else:
                    images = Variable(torch.from_numpy(images).type(torch.FloatTensor))
                    targets = [Variable(torch.from_numpy(ann).type(torch.FloatTensor)) for ann in targets]

            #----------------------#
            #   清零梯度
            #----------------------#
            optimizer.zero_grad()
            #----------------------#
            #   前向传播
            #----------------------#
            outputs = net(images)
            losses = []
            num_pos_all = 0
            #----------------------#
            #   计算损失
            #----------------------#
            for i in range(3):
                loss_item, num_pos = yolo_losses[i](outputs[i], targets)
                losses.append(loss_item)
                num_pos_all += num_pos

            loss = sum(losses) / num_pos_all
            #----------------------#
            #   反向传播
            #----------------------#
            loss.backward()
            optimizer.step()

            total_loss += loss.item()
            
            pbar.set_postfix(**{'total_loss': total_loss / (iteration + 1), 
                                'lr'        : get_lr(optimizer)})
            pbar.update(1)

    net.eval()
    print('Start Validation')
    with tqdm(total=epoch_size_val, desc=f'Epoch {epoch + 1}/{Epoch}',postfix=dict,mininterval=0.3) as pbar:
        for iteration, batch in enumerate(genval):
            if iteration >= epoch_size_val:
                break
            images_val, targets_val = batch[0], batch[1]

            with torch.no_grad():
                if cuda:
                    images_val = Variable(torch.from_numpy(images_val).type(torch.FloatTensor)).cuda()
                    targets_val = [Variable(torch.from_numpy(ann).type(torch.FloatTensor)) for ann in targets_val]
                else:
                    images_val = Variable(torch.from_numpy(images_val).type(torch.FloatTensor))
                    targets_val = [Variable(torch.from_numpy(ann).type(torch.FloatTensor)) for ann in targets_val]
                optimizer.zero_grad()
                outputs = net(images_val)
                losses = []
                num_pos_all = 0
                for i in range(3):
                    loss_item, num_pos = yolo_losses[i](outputs[i], targets_val)
                    losses.append(loss_item)
                    num_pos_all += num_pos
                loss = sum(losses) / num_pos_all
                val_loss += loss.item()
            pbar.set_postfix(**{'total_loss': val_loss / (iteration + 1)})
            pbar.update(1)
    Loss_list.append(val_loss/(epoch_size_val+1))

    checkpoint = {
        "net": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "epoch": epoch,
        "loss_list": np.array(Loss_list)
    }
    if not os.path.isdir("./nets/checkpoint"):
        os.mkdir("./nets/checkpoint")
    torch.save(checkpoint, checkpoint_path + '/model_epoch_%s.pth'%(str(epoch)))

    print('Finish Validation')
    print('Epoch:'+ str(epoch+1) + '/' + str(Epoch))
    print('Total Loss: %.4f || Val Loss: %.4f ' % (total_loss/(epoch_size+1),val_loss/(epoch_size_val+1)))
    print('Saving state, iter:', str(epoch+1))
    torch.save(model.state_dict(), model_savepath+'/Epoch%d-Total_Loss%.4f-Val_Loss%.4f.pth'%((epoch+1),total_loss/(epoch_size+1),val_loss/(epoch_size_val+1)))


#----------------------------------------------------#
#   检测精度mAP和pr曲线计算参考视频
#   https://www.bilibili.com/video/BV1zE411u7Vw
#----------------------------------------------------#
if __name__ == "__main__":
    #-------------------------------#
    #   所使用的主干特征提取网络
    #   mobilenetv1
    #   mobilenetv2
    #   mobilenetv3
    #-------------------------------#
    backbone = "mobilenetv3"
    model_path = "model_data/yolov4_mobilenet_v3_voc.pth"
    trained_model_path = ".\\logs\\0906-234137\\Epoch40-Total_Loss4.0172-Val_Loss1.4934.pth"
    #-------------------------------#
    #   是否使用主干网络的预训练权重
    #-------------------------------#
    pretrained = False
    #-------------------------------#
    #   是否使用Cuda
    #   没有GPU可以设置成False
    #-------------------------------#
    Cuda = True
    #-------------------------------#
    #   Dataloder的使用
    #-------------------------------#
    Use_Data_Loader = True
    #------------------------------------------------------#
    #   是否对损失进行归一化，用于改变loss的大小
    #   用于决定计算最终loss是除上batch_size还是除上正样本数量
    #------------------------------------------------------#
    normalize = False
    #-------------------------------#
    #   输入的shape大小
    #   显存比较小可以使用416x416
    #   显存比较大可以使用608x608
    #-------------------------------#
    input_shape = (416,416)

    #----------------------------------------------------#
    #   classes和anchor的路径，非常重要
    #   训练前一定要修改classes_path，使其对应自己的数据集
    #----------------------------------------------------#
    anchors_path = 'model_data/yolo_anchors.txt'
    classes_path = 'model_data/new_classes.txt'   
    #----------------------------------------------------#
    #   获取classes和anchor
    #----------------------------------------------------#
    class_names = get_classes(classes_path)
    print('classes names: ',class_names)
    anchors = get_anchors(anchors_path)
    num_classes = len(class_names)
    
    #------------------------------------------------------#
    #   Yolov4的tricks应用
    #   mosaic 马赛克数据增强 True or False 
    #   实际测试时mosaic数据增强并不稳定，所以默认为False
    #   Cosine_scheduler 余弦退火学习率 True or False
    #   label_smoothing 标签平滑 0.01以下一般 如0.01、0.005
    #------------------------------------------------------#
    mosaic = True
    Cosine_lr = False
    smoooth_label = 0

    #------------------------------------------------------#
    #   创建yolo模型
    #   训练前一定要修改classes_path和对应的txt文件
    #------------------------------------------------------#
    model = YoloBody(len(anchors[0]), num_classes, backbone=backbone, pretrained=pretrained)

    #------------------------------------------------------#
    #   权值文件请看README，百度网盘下载
    #------------------------------------------------------#
    # 加快模型训练的效率
    print('Loading weights into state dict...')
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model_dict = model.state_dict()
    # pretrained_dict = torch.load(model_path, map_location=device)
    pretrained_dict = torch.load(trained_model_path, map_location=device) # 加载已经训练好的模型
    pretrained_dict = {k: v for k, v in pretrained_dict.items() if np.shape(model_dict[k]) ==  np.shape(v)}
    # print(k for k,v in pretrained_dict.items() if not np.shape(model_dict[k]) ==  np.shape(v))
    model_dict.update(pretrained_dict)
    model.load_state_dict(model_dict)
    
    print('Finished!')

    # 只训练yolohead
    train_layer = ['yolo_head1','yolo_head2','yolo_head3']
    for name,param in model.named_parameters():
        if name.split('.')[0] in train_layer:
            continue
        else:
            param.requires_grad=False

    # for name,param in model.named_parameters():
    #     if param.requires_grad==False:
    #         print(name)

    net = model.train()

    if Cuda:
        net = torch.nn.DataParallel(model)
        cudnn.benchmark = True
        net = net.cuda()

    # 建立loss函数
    yolo_losses = []
    for i in range(3):
        yolo_losses.append(YOLOLoss(np.reshape(anchors,[-1,2]),num_classes, \
                                (input_shape[1], input_shape[0]), smoooth_label, Cuda, normalize))

    #----------------------------------------------------#
    #   获得图片路径和标签
    #----------------------------------------------------#
    annotation_path = '2007_train.txt'
    #----------------------------------------------------------------------#
    #   验证集的划分在train.py代码里面进行
    #   2007_test.txt和2007_val.txt里面没有内容是正常的。训练不会使用到。
    #   当前划分方式下，验证集和训练集的比例为1:9
    #----------------------------------------------------------------------#
    val_split = 0.2
    with open(annotation_path) as f:
        lines = f.readlines()
    np.random.seed(10101)
    np.random.shuffle(lines)
    np.random.seed(None)
    num_val = int(len(lines)*val_split)
    num_train = len(lines) - num_val
    Loss_list = []
    #------------------------------------------------------#
    #   主干特征提取网络特征通用，冻结训练可以加快训练速度
    #   也可以在训练初期防止权值被破坏。
    #   Init_Epoch为起始世代
    #   Freeze_Epoch为冻结训练的世代
    #   Epoch总训练世代
    #   提示OOM或者显存不足请调小Batch_size
    #------------------------------------------------------#
    checkpoint_path = './nets/checkpoint/'+datetime.strftime(datetime.now(), '%m%d-%H%M%S')
    model_savepath = './logs/'+datetime.strftime(datetime.now(), '%m%d-%H%M%S')
    if not os.path.exists(checkpoint_path):
        os.mkdir(checkpoint_path)
    if not os.path.exists(model_savepath):
        os.mkdir(model_savepath)

    # RESUME = True  #使用断点训练
    RESUME = False 
    Freeze_Epoch = 15
    Partly_Unfreeze_Epoch = 30
    Unfreeze_Epoch = 40
    Batch_size = 8
    # accumulation_steps = 2

    Freezing = True
    Partly_Unfreeze = False
    NeedInit = True

    if RESUME:
        lr = 1e-3
        Init_Epoch = 0
        optimizer = optim.Adam(net.parameters(),lr)
        # 加载断点
        path_checkpoint = "./nets/checkpoint/0906-224852/model_epoch_25.pth" #断点路径
        checkpoint = torch.load(path_checkpoint)
        # 恢复参数
        model.load_state_dict(checkpoint['net'])
        optimizer.load_state_dict(checkpoint['optimizer'])
        Init_Epoch = checkpoint['epoch']
        Loss_list = list(checkpoint['loss_list'])

        NeedInit = False

        if Init_Epoch < Freeze_Epoch:
            Freezing = True
        elif Init_Epoch < Partly_Unfreeze_Epoch:
            Freezing = False
            Partly_Unfreeze = True
        else:
            Freezing = False

    #------------------------------------#
    #   冻结一定部分训练                
    #------------------------------------#          
    if Freezing: # (断点epoch < Freeze_Epoch)
        if NeedInit:
            lr = 1e-3
            Init_Epoch = 0
            optimizer = optim.Adam(net.parameters(),lr)
        
        #----------------------------------------------------------------------------#
        #   我在实际测试时，发现optimizer的weight_decay起到了反作用，
        #   所以去除掉了weight_decay，大家也可以开起来试试，一般是weight_decay=5e-4
        #----------------------------------------------------------------------------#
        
        if Cosine_lr:
            lr_scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=5, eta_min=1e-5)
        else:
            lr_scheduler = optim.lr_scheduler.StepLR(optimizer,step_size=1,gamma=0.92)

        if Use_Data_Loader:
            train_dataset = YoloDataset(lines[:num_train], (input_shape[0], input_shape[1]), mosaic=mosaic, is_train=True)
            val_dataset = YoloDataset(lines[num_train:], (input_shape[0], input_shape[1]), mosaic=False, is_train=False)
            gen = DataLoader(train_dataset, shuffle=True, batch_size=Batch_size, num_workers=0, pin_memory=True,
                                    drop_last=True, collate_fn=yolo_dataset_collate)
            gen_val = DataLoader(val_dataset, shuffle=True, batch_size=Batch_size, num_workers=0,pin_memory=True, 
                                    drop_last=True, collate_fn=yolo_dataset_collate)
        else:
            gen = Generator(Batch_size, lines[:num_train],
                            (input_shape[0], input_shape[1])).generate(train=True, mosaic = mosaic)
            gen_val = Generator(Batch_size, lines[num_train:],
                            (input_shape[0], input_shape[1])).generate(train=False, mosaic = False)

        epoch_size = max(1, num_train//Batch_size)
        epoch_size_val = num_val//Batch_size
        
        # for param in model.backbone.parameters():
        #     param.requires_grad = False
        
        for epoch in range(Init_Epoch,Freeze_Epoch):
            fit_one_epoch(net,yolo_losses,epoch,epoch_size,epoch_size_val,gen,gen_val,Freeze_Epoch,Cuda)
            lr_scheduler.step()
            # if (epoch+1)%accumulation_steps == 0:
            #     optimizer.zero_grad()
            #     optimizer.step()
            #     lr_scheduler.step() 
            # print("第%d个epoch的学习率：%f" % (epoch, optimizer.param_groups[0]['lr']))

    #------------------------------------#
    #   部分(除backbone)解冻后训练
    #------------------------------------#
    if Partly_Unfreeze or Freezing:
        if Partly_Unfreeze:
            Freeze_Epoch = Init_Epoch
        lr = 1e-4
        Batch_size = 8
        # accumulation_steps = 2
        optimizer = optim.Adam(net.parameters(),lr)
        
    #----------------------------------------------------------------------------#
    #   我在实际测试时，发现optimizer的weight_decay起到了反作用，
    #   所以去除掉了weight_decay，大家也可以开起来试试，一般是weight_decay=5e-4
    #----------------------------------------------------------------------------#
    
        if Cosine_lr:
            lr_scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=5, eta_min=1e-5)
        else:
            lr_scheduler = optim.lr_scheduler.StepLR(optimizer,step_size=1,gamma=0.92)

        if Use_Data_Loader:
            train_dataset = YoloDataset(lines[:num_train], (input_shape[0], input_shape[1]), mosaic=mosaic, is_train=True)
            val_dataset = YoloDataset(lines[num_train:], (input_shape[0], input_shape[1]), mosaic=False, is_train=False)
            gen = DataLoader(train_dataset, shuffle=True, batch_size=Batch_size, num_workers=0, pin_memory=True,
                                    drop_last=True, collate_fn=yolo_dataset_collate)
            gen_val = DataLoader(val_dataset, shuffle=True, batch_size=Batch_size, num_workers=0,pin_memory=True, 
                                    drop_last=True, collate_fn=yolo_dataset_collate)
        else:
            gen = Generator(Batch_size, lines[:num_train],
                            (input_shape[0], input_shape[1])).generate(train=True, mosaic = mosaic)
            gen_val = Generator(Batch_size, lines[num_train:],
                            (input_shape[0], input_shape[1])).generate(train=False, mosaic = False)

        epoch_size = max(1, num_train//Batch_size)
        epoch_size_val = num_val//Batch_size
    
    for name,param in model.named_parameters():
        if name.split('.')[0] in train_layer or name.split('.')[0] == 'backbone':
            continue
        else: 
            param.requires_grad = True

    for epoch in range(Freeze_Epoch,Partly_Unfreeze_Epoch):
        fit_one_epoch(net,yolo_losses,epoch,epoch_size,epoch_size_val,gen,gen_val,Partly_Unfreeze_Epoch,Cuda)
        lr_scheduler.step()
        # if (epoch+1)%accumulation_steps == 0:
        #     optimizer.zero_grad()
        #     optimizer.step()
        #     lr_scheduler.step() 
        # print("第%d个epoch的学习率：%f" % (epoch, optimizer.param_groups[0]['lr']))


    #------------------------------------#
    #   backbone解冻后训练
    #------------------------------------#
    for param in model.backbone.parameters():
        param.requires_grad = True

    if not(Partly_Unfreeze or Freezing):
        Partly_Unfreeze_Epoch = Init_Epoch

    lr = 1e-6
    Batch_size = 8
    # accumulation_steps = 2
    optimizer = optim.Adam(net.parameters(),lr)
    
#----------------------------------------------------------------------------#
#   我在实际测试时，发现optimizer的weight_decay起到了反作用，
#   所以去除掉了weight_decay，大家也可以开起来试试，一般是weight_decay=5e-4
#----------------------------------------------------------------------------#

    if Cosine_lr:
        lr_scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=5, eta_min=1e-5)
    else:
        lr_scheduler = optim.lr_scheduler.StepLR(optimizer,step_size=1,gamma=0.92)

    if Use_Data_Loader:
        train_dataset = YoloDataset(lines[:num_train], (input_shape[0], input_shape[1]), mosaic=mosaic, is_train=True)
        val_dataset = YoloDataset(lines[num_train:], (input_shape[0], input_shape[1]), mosaic=False, is_train=False)
        gen = DataLoader(train_dataset, shuffle=True, batch_size=Batch_size, num_workers=0, pin_memory=True,
                                drop_last=True, collate_fn=yolo_dataset_collate)
        gen_val = DataLoader(val_dataset, shuffle=True, batch_size=Batch_size, num_workers=0,pin_memory=True, 
                                drop_last=True, collate_fn=yolo_dataset_collate)
    else:
        gen = Generator(Batch_size, lines[:num_train],
                        (input_shape[0], input_shape[1])).generate(train=True, mosaic = mosaic)
        gen_val = Generator(Batch_size, lines[num_train:],
                        (input_shape[0], input_shape[1])).generate(train=False, mosaic = False)

    epoch_size = max(1, num_train//Batch_size)
    epoch_size_val = num_val//Batch_size

    for epoch in range(Partly_Unfreeze_Epoch,Unfreeze_Epoch):
        fit_one_epoch(net,yolo_losses,epoch,epoch_size,epoch_size_val,gen,gen_val,Unfreeze_Epoch,Cuda)
        lr_scheduler.step()
        # if (epoch+1)%accumulation_steps == 0:
        #     optimizer.zero_grad()
        #     optimizer.step()
        #     lr_scheduler.step() 
        # print("第%d个epoch的学习率：%f" % (epoch, optimizer.param_groups[0]['lr']))


    plt.plot(range(Init_Epoch,Unfreeze_Epoch),Loss_list)
    plt.savefig(model_savepath+'/loss.jpg')
    plt.show()
