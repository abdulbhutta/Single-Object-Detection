from __future__ import division
from __future__ import print_function

import argparse
import os
import cv2
import numpy as np
import torch

import sys 
sys.path.append(os.path.abspath('.'))

from toolkit.datasets import DatasetFactory
from toolkit.utils.region import vot_overlap, vot_float2str
from nanotrack.models.model_builder import ModelBuilder
from nanotrack.tracker.tracker_builder import build_tracker
from nanotrack.utils.bbox import get_axis_aligned_bbox
from nanotrack.utils.model_load import load_pretrain
from nanotrack.core.config import cfg

torch.set_num_threads(1)

def parse_range(range_str): 
    param = list(map(float, range_str.strip().split(',')))
   
    return np.array(param)

def parse_range_int(range_str):
    param = list(map(int, range_str.strip().split(',')))
  
    return np.array(param) 

parser = argparse.ArgumentParser(description='Hyperparamter Search')
parser.add_argument('--snapshot', default='models/pretrained/nanotrackv1.pth',type=str, help='snapshot of model')
parser.add_argument('--dataset', default='VOT2018',type=str, help='dataset name to eval')
parser.add_argument('--penalty-k', default='0.145, 0.148, 0.150, 0.152, 0.155', type=parse_range) 
parser.add_argument('--lr', default='0.385, 0.390, 0.395, 0.400, 0.405, 0.410, 0.415, 0.420', type=parse_range)  
parser.add_argument('--window-influence', default='0.462, 0.465, 0.468, 0.470, 0.472, 0.475', type=parse_range) #0.40  

parser.add_argument('--search-region', default='255', type=parse_range_int) 
parser.add_argument('--config', default='./models/config/configv1.yaml', type=str)  
args = parser.parse_args() 

os.environ["CUDA_VISIBLE_DEVICES"] = "0"    

def run_tracker(tracker, img, gt, video_name, restart=True):
    frame_counter = 0
    lost_number = 0
    toc = 0
    pred_bboxes = []
    if restart:  
        for idx, (img, gt_bbox) in enumerate(video):
            if len(gt_bbox) == 4:
                gt_bbox = [gt_bbox[0], gt_bbox[1],
                           gt_bbox[0], gt_bbox[1]+gt_bbox[3]-1,
                           gt_bbox[0]+gt_bbox[2]-1, gt_bbox[1]+gt_bbox[3]-1,
                           gt_bbox[0]+gt_bbox[2]-1, gt_bbox[1]]
            tic = cv2.getTickCount()
            if idx == frame_counter:
                cx, cy, w, h = get_axis_aligned_bbox(np.array(gt_bbox))
                gt_bbox_ = [cx-(w-1)/2, cy-(h-1)/2, w, h]
                tracker.init(img, gt_bbox_)
                pred_bbox = gt_bbox_
                pred_bboxes.append(1)
            elif idx > frame_counter:
                outputs = tracker.track(img)
                pred_bbox = outputs['bbox']
                overlap = vot_overlap(pred_bbox, gt_bbox,
                                      (img.shape[1], img.shape[0]))
                if overlap > 0:
                    
                    pred_bboxes.append(pred_bbox)
                else:
                    
                    pred_bboxes.append(2)
                    frame_counter = idx + 5 
                    lost_number += 1
            else:
                pred_bboxes.append(0)
            toc += cv2.getTickCount() - tic
        toc /= cv2.getTickFrequency()
        print('Video: {:12s} Time: {:4.1f}s Speed: {:3.1f}fps Lost: {:d}'.format(
            video_name, toc, idx / toc, lost_number))
        return pred_bboxes
    else:
        toc = 0
        pred_bboxes = []
        scores = []
        track_times = []
        for idx, (img, gt_bbox) in enumerate(video):
            tic = cv2.getTickCount()
            if idx == 0:
                cx, cy, w, h = get_axis_aligned_bbox(np.array(gt_bbox))
                gt_bbox_ = [cx-(w-1)/2, cy-(h-1)/2, w, h]
                tracker.init(img, gt_bbox_)
                pred_bbox = gt_bbox_
                scores.append(None)
                pred_bboxes.append(pred_bbox)
            else:
                outputs = tracker.track(img)
                pred_bbox = outputs['bbox']
                pred_bboxes.append(pred_bbox)
                scores.append(outputs['best_score'])
            toc += cv2.getTickCount() - tic
            track_times.append((cv2.getTickCount() - tic)/cv2.getTickFrequency())
        toc /= cv2.getTickFrequency()
        print('Video: {:12s} Time: {:5.1f}s Speed: {:3.1f}fps'.format(
            video_name, toc, idx / toc))
        return pred_bboxes, scores, track_times

def _check_and_occupation(video_path, result_path):
    if os.path.isfile(result_path):
        return True
    try:
        if not os.path.isdir(video_path):
            os.makedirs(video_path)
    except OSError as err:
        print(err)

    with open(result_path, 'w') as f:
        f.write('Occ')
    return False

if __name__ == '__main__':
   
    num_search = len(args.penalty_k) \
               * len(args.window_influence) \
               * len(args.lr) \
               * len(args.search_region)
    print("Total search number: {}".format(num_search))

    cfg.merge_from_file(args.config)

    cur_dir = os.path.dirname(os.path.realpath(__file__))
    dataset_root = os.path.join(cur_dir, '../data', args.dataset)

    # create dataset
    dataset = DatasetFactory.create_dataset(name=args.dataset,
                                            dataset_root=dataset_root,
                                            load_img=False)

    # create model
    model = ModelBuilder()

    # load model
    model = load_pretrain(model, args.snapshot).cuda().eval()

    # build tracker
    tracker = build_tracker(model)
    
    model_name = args.snapshot.split('/')[-1].split('.')[0]
    benchmark_path = os.path.join('hp_search_result', args.dataset)
    seqs = list(range(len(dataset)))
    np.random.shuffle(seqs)
    for idx in seqs:
        video = dataset[idx]
        
        video.load_img()
        np.random.shuffle(args.penalty_k)
        np.random.shuffle(args.window_influence)
        np.random.shuffle(args.lr)
        for pk in args.penalty_k:
            for wi in args.window_influence:
                for lr in args.lr:
                    for ins in args.search_region:
                        cfg.TRACK.PENALTY_K = float(pk)
                        cfg.TRACK.WINDOW_INFLUENCE = float(wi)
                        cfg.TRACK.LR = float(lr)
                        cfg.TRACK.INSTANCE_SIZE = int(ins)
                        
                        tracker = build_tracker(model)
                        tracker_path = os.path.join(benchmark_path,
                                (model_name +
                                 '_r{}'.format(ins) +
                                 '_pk-{:.4f}'.format(pk) +
                                 '_wi-{:.4f}'.format(wi) +
                                 '_lr-{:.4f}'.format(lr)))
                        if 'VOT2016' == args.dataset or 'VOT2018' == args.dataset or 'VOT2019' == args.dataset:
                            video_path = os.path.join(tracker_path, 'baseline', video.name)
                            result_path = os.path.join(video_path, video.name + '_001.txt')
                            if _check_and_occupation(video_path, result_path):
                                continue
                            pred_bboxes = run_tracker(tracker, video.imgs,
                                    video.gt_traj, video.name, restart=True)
                            with open(result_path, 'w') as f:
                                for x in pred_bboxes:
                                    if isinstance(x, int):
                                        f.write("{:d}\n".format(x))
                                    else:
                                        f.write(','.join([vot_float2str("%.4f", i) for i in x])+'\n')
                        elif 'VOT2018-LT' == args.dataset:
                            video_path = os.path.join(tracker_path, 'longterm', video.name)
                            result_path = os.path.join(video_path, '{}_001.txt'.format(video.name))
                            if _check_and_occupation(video_path, result_path):
                                continue
                            pred_bboxes, scores, track_times = run_tracker(tracker,
                                    video.imgs, video.gt_traj, video.name, restart=False)
                            pred_bboxes[0] = [0]
                            with open(result_path, 'w') as f:
                                for x in pred_bboxes:
                                    f.write(','.join([str(i) for i in x])+'\n')
                            result_path = os.path.join(video_path,
                                    '{}_001_confidence.value'.format(video.name))
                            with open(result_path, 'w') as f:
                                for x in scores:
                                    f.write('\n') if x is None else f.write("{:.6f}\n".format(x))
                            result_path = os.path.join(video_path,
                                    '{}_time.txt'.format(video.name))
                            with open(result_path, 'w') as f:
                                for x in track_times:
                                    f.write("{:.6f}\n".format(x))
                        elif 'GOT-10k' == args.dataset:
                            video_path = os.path.join('epoch_result', tracker_path, video.name)
                            if not os.path.isdir(video_path):
                                os.makedirs(video_path)
                            result_path = os.path.join(video_path, '{}_001.txt'.format(video.name))
                            with open(result_path, 'w') as f:
                                for x in pred_bboxes:
                                    f.write(','.join([str(i) for i in x])+'\n')
                            result_path = os.path.join(video_path,
                                    '{}_time.txt'.format(video.name))
                            with open(result_path, 'w') as f:
                                for x in track_times:
                                    f.write("{:.6f}\n".format(x))
                        else:
                            result_path = os.path.join(tracker_path, '{}.txt'.format(video.name))
                            if _check_and_occupation(tracker_path, result_path):
                                continue
                            pred_bboxes, _, _ = run_tracker(tracker, video.imgs,
                                    video.gt_traj, video.name, restart=False)
                            with open(result_path, 'w') as f:
                                for x in pred_bboxes:
                                    f.write(','.join([str(i) for i in x])+'\n')
       
        video.free_img()
