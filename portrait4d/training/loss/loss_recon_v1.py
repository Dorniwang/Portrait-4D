# Loss for Portrait4D, modified from EG3D: https://github.com/NVlabs/eg3d

# SPDX-FileCopyrightText: Copyright (c) 2021-2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""Loss functions."""

import numpy as np
import PIL
import torch
import torch.nn.functional as F
import torchvision.transforms as transform
from kornia.geometry import warp_affine
from torch_utils import training_stats
from torch_utils.ops import conv2d_gradfix
from torch_utils.ops import upfirdn2d
from training.discriminator.dual_discriminator import filtered_resizing
from training.utils.preprocess import estimate_norm_torch, estimate_norm_torch_pdfgc
from camera_utils import LookAtPoseSampler, FOV_to_intrinsics

#----------------------------------------------------------------------------

class Loss:
    def accumulate_gradients(self, phase, real_img, real_c, gen_z, gen_c, gain, cur_nimg): # to be overridden by subclass
        raise NotImplementedError()

#----------------------------------------------------------------------------

class AnimatablePortraitReconLoss(Loss):
    def __init__(self, device, G, D, G_syn, D_patch=None, augment_pipe=None, lpips=None, facenet=None, pd_fgc=None, gmain=1.0, r1_gamma=10, r1_gamma_patch=10, r1_gamma_uv=30, r1_gamma_seg=10, style_mixing_prob=0, pl_weight=0, pl_batch_shrink=2, pl_decay=0.01, pl_no_weight_grad=False, blur_init_sigma=0, blur_init_sigma_patch=0, blur_fade_kimg=0, blur_patch_seg=0, r1_gamma_init=0, r1_gamma_fade_kimg=0, neural_rendering_resolution_initial=64, neural_rendering_resolution_final=None, neural_rendering_resolution_fade_kimg=0, gpc_reg_fade_kimg=1000, gpc_reg_prob=None, discrimination_kimg=1000, dual_discrimination=False, filter_mode='antialiased', patch_scale=1.0, patch_gan=0.2, masked_sampling=None, perturb_params=False, use_D=True, truncation_psi=0.7):
        super().__init__()
        self.device             = device
        self.G                  = G
        self.D                  = D
        self.G_syn              = G_syn
        self.D_patch            = D_patch
        self.augment_pipe       = augment_pipe
        self.lpips              = lpips
        self.facenet            = facenet
        self.pd_fgc             = pd_fgc
        self.gmain              = gmain
        self.r1_gamma           = r1_gamma
        self.r1_gamma_patch     = r1_gamma_patch
        self.r1_gamma_uv           = r1_gamma_uv
        self.r1_gamma_seg       = r1_gamma_seg
        self.style_mixing_prob  = style_mixing_prob
        self.pl_weight          = pl_weight
        self.pl_batch_shrink    = pl_batch_shrink
        self.pl_decay           = pl_decay
        self.pl_no_weight_grad  = pl_no_weight_grad
        self.pl_mean            = torch.zeros([], device=device)
        self.blur_init_sigma    = blur_init_sigma
        self.blur_init_sigma_patch = blur_init_sigma_patch
        self.blur_fade_kimg     = blur_fade_kimg
        self.blur_patch_seg     = blur_patch_seg
        self.r1_gamma_init      = r1_gamma_init
        self.r1_gamma_fade_kimg = r1_gamma_fade_kimg
        self.bg_reg             = True
        self.c_headpose         = False
        self.neural_rendering_resolution_initial = neural_rendering_resolution_initial
        self.neural_rendering_resolution_final = neural_rendering_resolution_final
        self.neural_rendering_resolution_fade_kimg = neural_rendering_resolution_fade_kimg
        self.gpc_reg_fade_kimg = gpc_reg_fade_kimg
        self.gpc_reg_prob = gpc_reg_prob
        self.discrimination_kimg = discrimination_kimg
        self.dual_discrimination = dual_discrimination
        self.filter_mode = filter_mode
        self.resample_filter = upfirdn2d.setup_filter([1,3,3,1], device=device)
        self.blur_raw_target = True
        assert self.gpc_reg_prob is None or (0 <= self.gpc_reg_prob <= 1)
        self.patch_scale = patch_scale
        self.masked_sampling = masked_sampling
        self.patch_gan = patch_gan
        self.perturb_params = perturb_params
        self.use_D = use_D
        self.truncation_psi = truncation_psi
    
    # extract pdfgc motion embedding
    def get_motion_feature(self, imgs, lmks, crop_size=224, crop_len=16):

        trans_m = estimate_norm_torch_pdfgc(lmks, imgs.shape[-1])
        imgs_warp = warp_affine(imgs, trans_m, dsize=(224, 224))
        imgs_warp = imgs_warp[:,:,:crop_size - crop_len*2, crop_len:crop_size - crop_len]
        imgs_warp = torch.clamp(F.interpolate(imgs_warp,size=[crop_size,crop_size],mode='bilinear'),-1,1)
             
        out = self.pd_fgc(imgs_warp)
        motions = torch.cat([out[1],out[2],out[3]],dim=-1)

        return motions
    
    # generate online training data using pre-trained GenHead generator
    @torch.no_grad()
    def gen_data_by_G_syn(self, z, shape_params, exp_params, pose_params, eye_pose_params, c):

        batchsize_subject = shape_params.shape[0]
        c_cond = c[:,0:1].repeat(1,3,1)
        c_cond = c_cond.reshape(-1, *c_cond.shape[2:]) 

        z = z.reshape(-1,*z.shape[2:]) # (b*3, 512)
        shape_params = shape_params.reshape(-1,*shape_params.shape[2:]) # (b*3, 300)
        exp_params = exp_params.reshape(-1,*exp_params.shape[2:]) # (b*3, 100)
        pose_params = pose_params.reshape(-1,*pose_params.shape[2:]) # (b*3, 6)
        eye_pose_params = eye_pose_params.reshape(-1,*eye_pose_params.shape[2:]) # (b*3, 6)
        c = c.reshape(-1,*c.shape[2:]) # (b*3, 25)
        
        # random head rotation
        angle_ys_head = torch.rand((shape_params.shape[0], 1),device=shape_params.device)*0.78*2 - 0.78
        angle_ys_head2 = torch.rand((shape_params.shape[0], 1),device=shape_params.device)*0.45*2 - 0.45 + 0.2
        angle_ys_head3 = torch.rand((shape_params.shape[0], 1),device=shape_params.device)*0.25*2 - 0.25
        
        # random camera pose
        cam_pivot_x = torch.rand((shape_params.shape[0], 1),device=shape_params.device)*0.02 - 0.01
        cam_pivot_y = torch.rand((shape_params.shape[0], 1),device=shape_params.device)*0.02 - 0.01
        cam_pivot_z = torch.rand((shape_params.shape[0], 1),device=shape_params.device)*0.02 - 0.01 + 0.03
        cam_pivot = torch.cat([cam_pivot_x*3, cam_pivot_y*3, cam_pivot_z*3],dim=-1)
        cam_radius = torch.rand((shape_params.shape[0], 1),device=shape_params.device)*0.8 + 3.65
        cam2world_pose = LookAtPoseSampler.sample(np.pi/2+angle_ys_head, np.pi/2-angle_ys_head2, cam_pivot, radius=cam_radius, roll_mean=angle_ys_head3, batch_size=shape_params.shape[0], device=shape_params.device)
        
        c_syn = torch.cat([cam2world_pose.reshape(-1,16),c[:,16:].reshape(-1,9)],dim=-1)

        prob = torch.rand((c.shape[0], 1), device=c.device)
        c_syn = torch.where(prob < 0.5, c_syn, c)

        c_syn_decompose = self.G_syn.deformer.flame_deform.decompose_camera_pose(c_syn, shape_params,exp_params,pose_params,use_rotation_limits=False)
        
        # camera pose condition for mapping net
        c_cond_syn = torch.eye(4).unsqueeze(0).repeat(shape_params.shape[0],1,1).to(shape_params.device)
        c_cond_syn[...,2,3] += 4
        c_cond_syn = torch.cat([c_cond_syn.reshape(-1,16),c[:,16:].reshape(-1,9)],dim=-1)
        prob = torch.rand((c.shape[0], 1), device=c.device)
        c_cond_syn = torch.where(prob < 0.5, c_cond_syn, c_cond)

        if self.G_syn.flame_condition:
            shape_params_cond = shape_params
            c_cond = torch.cat([c_cond,shape_params_cond],dim=-1)

        _deformer = self.G_syn._deformer(shape_params, exp_params, pose_params, eye_pose_params, use_rotation_limits=False)
        ws = self.G_syn.mapping(z, c_cond, truncation_psi=self.truncation_psi, truncation_cutoff=14)
        ws_avg = self.G_syn.mapping(z, c_cond, truncation_psi=0, truncation_cutoff=14)
        out = self.G_syn.synthesis(ws, z, c_syn_decompose, _deformer, ws_super=ws_avg, neural_rendering_resolution=64, noise_mode='const', smpl_param=(shape_params, exp_params, pose_params, eye_pose_params),eye_blink_params=None)        
        out['c'] = c_syn_decompose
        out['c_compose'] = c_syn

        render_out = self.G_syn.deformer.renderer(shape_params, exp_params, pose_params, eye_pose_params, c_syn, half_size = int(self.G_syn.img_resolution/2))
        uv = render_out[0]
        landmarks = render_out[-1]
        
        out['uv'] = uv
        out['landmarks'] = landmarks
        
        # extract pdfgc motion feature
        motions = self.get_motion_feature(out['image_sr'], landmarks)
        out['motions'] = motions

        return out

    def run_G(self, imgs_app, imgs_mot, shape_params_app, exp_params_mot, pose_params_mot, eye_pose_params_mot, motions_app, motions, c, neural_rendering_resolution, neural_rendering_resolution_patch, patch_scale=1.0, run_full=True, update_emas=False, motion_scale=1.0, swapping_prob=0.5, half_static=False):

        render_out = self.G.deformer.renderer(shape_params_app, exp_params_mot, pose_params_mot, eye_pose_params_mot, c, half_size = int(self.G.img_resolution/2))
        uv = render_out[0]
        landmarks = render_out[-1]
        _deformer = self.G._deformer(shape_params_app,exp_params_mot,pose_params_mot,eye_pose_params_mot)

        motion_scale = torch.ones([imgs_app.shape[0],1,1], device=c.device)*motion_scale
        if swapping_prob is not None:
            imgs_app_swapped = imgs_mot
            prob = torch.rand((imgs_app.shape[0], 1), device=c.device)
            imgs_app_conditioning = torch.where(prob.reshape(imgs_app.shape[0],1,1,1) < swapping_prob, imgs_app_swapped, imgs_app)
            motion_scale_conditioning = torch.where(prob.reshape(imgs_app.shape[0],1,1) < swapping_prob, torch.zeros_like(motion_scale), motion_scale)
            motions_app_conditioning = torch.where(prob < swapping_prob, motions, motions_app)
        else:
            imgs_app_conditioning = imgs_app
            motion_scale_conditioning = motion_scale
            motions_app_conditioning = motions_app
        
        # whether or not the second half of the batchsize are static data
        # If true, set motion scale to zero to deactivate motion-related cross-attention layers. 
        if half_static:
            num_static = imgs_app.shape[0]//2
            if swapping_prob is None:
                motion_scale_conditioning = torch.cat([motion_scale[:num_static],motion_scale[num_static:]*0],dim=0)
            else:
                prob = torch.rand((num_static, 1), device=c.device)
                motion_scale_static = torch.where(prob.reshape(num_static,1,1) < 1 - swapping_prob, torch.zeros_like(motion_scale[num_static:]), motion_scale[num_static:])
                motion_scale_conditioning = torch.cat([motion_scale_conditioning[:num_static],motion_scale_static],dim=0)

        gen_output = self.G.synthesis(imgs_app_conditioning, imgs_mot, motions_app_conditioning, motions, c, _deformer=_deformer, neural_rendering_resolution=neural_rendering_resolution, motion_scale=motion_scale_conditioning)
        gen_output['uv'] = uv
        gen_output['landmarks'] = landmarks
         
        return gen_output

    def run_D(self, img, c, blur_sigma=0, blur_sigma_raw=0, update_emas=False):
        blur_size = np.floor(blur_sigma * 3)
        if blur_size > 0:
            with torch.autograd.profiler.record_function('blur'):
                if self.G.has_superresolution:
                    f = torch.arange(-blur_size, blur_size + 1, device=img['image_sr'].device).div(blur_sigma).square().neg().exp2()
                    img['image_sr'] = upfirdn2d.filter2d(img['image_sr'], f / f.sum())
                else:
                    f = torch.arange(-blur_size, blur_size + 1, device=img['image'].device).div(blur_sigma).square().neg().exp2()
                    img['image'] = upfirdn2d.filter2d(img['image'], f / f.sum())

        logits = self.D(img, c, update_emas=update_emas)
        return logits

    def accumulate_gradients(self, phase, real_img_app, real_img_mot, real_img_recon, real_seg_recon, real_seg_recon_render, real_uv_recon, real_c_recon, real_c_compose_recon, shape_params_app, exp_params_mot, pose_params_mot, eye_pose_params_mot, motions_app, motions, gain, cur_nimg, real_depth_recon=None, real_feature_recon=None, real_feature_recon_bg=None, real_triplane_recon=None, motion_scale=1.0, swapping_prob=0.5, half_static=False):

        if self.G.rendering_kwargs.get('density_reg', 0) == 0:
            phase = {'Greg': 'none', 'Gboth': 'Gmain'}.get(phase, phase)
        if self.r1_gamma == 0:
            phase = {'Dreg': 'none', 'Dboth': 'Dmain'}.get(phase, phase)
        if self.r1_gamma_patch == 0:
            phase = {'D_patchreg': 'none', 'D_patchboth': 'Dmain'}.get(phase, phase)

        blur_sigma = 0
        r1_gamma = self.r1_gamma
        r1_gamma_patch = self.r1_gamma_patch
        r1_gamma_uv = self.r1_gamma_uv
        r1_gamma_seg = self.r1_gamma_seg
   
        if self.neural_rendering_resolution_final is not None:
            alpha = min(max((cur_nimg - self.discrimination_kimg * 1e3) / (self.neural_rendering_resolution_fade_kimg * 1e3), 0), 1) # begin fading when D starts to be optimized
            neural_rendering_resolution = int(np.rint(self.neural_rendering_resolution_initial * (1 - alpha) + self.neural_rendering_resolution_final * alpha))
            neural_rendering_resolution_patch = self.neural_rendering_resolution_final
        else:
            neural_rendering_resolution = self.neural_rendering_resolution_initial
            neural_rendering_resolution_patch = neural_rendering_resolution
        

        if self.G.has_superresolution:
            real_img_raw = filtered_resizing(real_img_recon, size=neural_rendering_resolution, f=self.resample_filter, filter_mode=self.filter_mode)
            real_seg_raw = filtered_resizing(real_seg_recon, size=neural_rendering_resolution, f=self.resample_filter, filter_mode=self.filter_mode)

            if real_seg_recon_render is None:
                real_seg_recon_render = real_seg_raw
            else:
                real_seg_recon_render = filtered_resizing(real_seg_recon_render, size=neural_rendering_resolution, f=self.resample_filter, filter_mode=self.filter_mode)
            
            if real_feature_recon_bg is not None:
                real_feature_recon_bg = filtered_resizing(real_feature_recon_bg, size=neural_rendering_resolution, f=self.resample_filter, filter_mode=self.filter_mode)

            if self.blur_raw_target and blur_sigma > 0:
                blur_size = np.floor(blur_sigma * 3)
                if blur_size > 0:
                    f = torch.arange(-blur_size, blur_size + 1, device=real_img_raw.device).div(blur_sigma).square().neg().exp2()
                    real_img_raw = upfirdn2d.filter2d(real_img_raw, f / f.sum())
                    real_seg_raw = upfirdn2d.filter2d(real_seg_raw, f / f.sum())

            real_img = {'image_sr': real_img_recon, 'uv': real_uv_recon, 'image': real_img_raw, 'seg': torch.mean(real_seg_recon_render,dim=1,keepdim=True)}
        else:
            real_img = {'image': real_img_recon, 'uv': real_uv_recon, 'seg': torch.mean(real_seg_recon_render,dim=1,keepdim=True)}

        real_img_recon_wobg_raw = (real_img_raw + 1) * 0.5 * (real_seg_recon_render + 1) * 0.5
        real_img_recon_wobg_raw = 2 * real_img_recon_wobg_raw - 1
        real_img_bg_raw = (real_img_raw + 1) * 0.5 * (1 - (real_seg_recon_render + 1) * 0.5)
        real_img_bg_raw = 2 * real_img_bg_raw - 1
        

        # Gmain: Maximize logits for generated images.
        if phase in ['Gmain', 'Gboth']:
            with torch.autograd.profiler.record_function('Gmain_forward'):
                gen_img = self.run_G(real_img_app, real_img_mot, shape_params_app, exp_params_mot, pose_params_mot, eye_pose_params_mot, motions_app, motions, real_c_recon, patch_scale=self.patch_scale, run_full=True, neural_rendering_resolution=neural_rendering_resolution, neural_rendering_resolution_patch=neural_rendering_resolution_patch, motion_scale=motion_scale, swapping_prob=swapping_prob, half_static=half_static)
                
                # main image-level reconstruction loss
                gen_img_recon = gen_img['image_sr']
                gen_img_recon_raw = gen_img['image']
                gen_img_recon_raw_wobg = gen_img['image_wobg']
                gen_seg = gen_img['seg']
                gen_depth = gen_img['image_depth']
                gen_feature = gen_img['image_feature']
                gen_feature_wobg = gen_img['image_feature_wobg']
                gen_feature_bg = gen_img['background_feature']
                gen_img_bg = gen_img['rgb_bg']
                gen_triplane = gen_img['planes']
                landmarks = gen_img['landmarks']

                if real_feature_recon_bg is None:
                    loss_recon_lpips = self.lpips(gen_img_recon, real_img_recon) + self.lpips(gen_img_recon_raw, real_img_raw) + self.lpips(gen_img_recon_raw_wobg, real_img_recon_wobg_raw) + self.lpips(((gen_img_bg + 1) * 0.5 * (1 - (real_seg_recon_render + 1) * 0.5))*2-1, real_img_bg_raw)
                else:
                    loss_recon_lpips = self.lpips(gen_img_recon, real_img_recon) + self.lpips(gen_img_recon_raw, real_img_raw) + self.lpips(gen_img_recon_raw_wobg, real_img_recon_wobg_raw) + self.lpips(gen_img_bg, real_feature_recon_bg[:, :3])

                training_stats.report('Loss/G/lrecon_lpips', loss_recon_lpips)
                
                if real_feature_recon_bg is None:
                    loss_recon_l1 = torch.abs(gen_img_recon-real_img_recon).mean() + torch.abs(gen_img_recon_raw-real_img_raw).mean() + torch.abs(gen_img_recon_raw_wobg-real_img_recon_wobg_raw).mean() + torch.abs(((gen_img_bg + 1) * 0.5 * (1 - (real_seg_recon_render + 1) * 0.5))*2-1 - real_img_bg_raw).mean()
                else:
                    loss_recon_l1 = torch.abs(gen_img_recon-real_img_recon).mean() + torch.abs(gen_img_recon_raw-real_img_raw).mean() + torch.abs(gen_img_recon_raw_wobg-real_img_recon_wobg_raw).mean() + torch.abs(gen_img_bg - real_feature_recon_bg[:, :3]).mean()

                training_stats.report('Loss/G/lrecon_l1', loss_recon_l1)
                
                # image alignment before calculating id feature
                trans_m = estimate_norm_torch(landmarks, real_img_recon.shape[-1])
                gen_img_recon_warp = warp_affine(gen_img_recon, trans_m, dsize=(112, 112))
                real_img_recon_warp = warp_affine(real_img_recon, trans_m, dsize=(112, 112))
                gen_id = self.facenet(torch.clamp(gen_img_recon_warp,-1,1))
                real_id = self.facenet(torch.clamp(real_img_recon_warp,-1,1).detach())

                loss_id = torch.mean(1 - F.cosine_similarity(gen_id, real_id, dim=-1))     
                training_stats.report('Loss/G/l_id', loss_id)
                
                # use id loss after seeing 400k images
                if cur_nimg < 400 * 1e3:
                    loss_id = 0

                loss_recon_seg = torch.abs(real_seg_recon_render-gen_seg).mean()
                training_stats.report('Loss/G/lrecon_seg', loss_recon_seg)
                
                # use depth loss before seeing 400k images
                if real_depth_recon is not None and cur_nimg < 400 * 1e3:
                    loss_recon_depth = torch.abs((real_depth_recon-gen_depth)*(real_seg_raw[:,0:1] + 1) * 0.5).mean()
                    training_stats.report('Loss/G/lrecon_depth', loss_recon_depth)
                else:
                    loss_recon_depth = 0.
                    
                # use feature map loss before seeing 400k images
                if real_feature_recon is not None and cur_nimg < 400 * 1e3:
                    real_feature_recon = (real_feature_recon + 1) * 0.5 * (real_seg_recon_render[:,0:1] + 1) * 0.5
                    real_feature_recon = 2 * real_feature_recon - 1

                    loss_recon_feature = torch.abs(real_feature_recon-gen_feature_wobg).mean() + torch.abs(real_feature_recon_bg-gen_feature_bg).mean()
                    training_stats.report('Loss/G/lrecon_feature', loss_recon_feature)
                else:
                    loss_recon_feature = 0.
                
                # use triplane feature loss before seeing 400k images
                if real_triplane_recon is not None and cur_nimg < 400 * 1e3:
                    coordinates = real_triplane_recon[...,:3]
                    _deformer = self.G._deformer(shape_params_app, exp_params_mot, pose_params_mot, eye_pose_params_mot)
                    out_deform = _deformer(coordinates)
                    coordinates = out_deform['canonical'] 
                    out = self.G.renderer.run_model(gen_triplane, None, coordinates, torch.zeros_like(coordinates), self.G.rendering_kwargs)
                    gen_triplane_recon = out['triplane_feature']
                    loss_recon_triplane = torch.abs(real_triplane_recon[...,3:]-gen_triplane_recon).mean()
                    training_stats.report('Loss/G/lrecon_triplane', loss_recon_triplane)
                else:
                    loss_recon_triplane = 0.

                loss_recon = loss_recon_lpips + loss_recon_l1 + loss_recon_seg*0.3 + loss_recon_depth + loss_recon_feature + loss_recon_triplane*0.1 + loss_id

                # adversarial loss after warm-up stage
                if cur_nimg >= self.discrimination_kimg * 1e3 and self.use_D:
                    c_compose_condition = real_c_compose_recon.clone()
                    gen_logits = self.run_D(gen_img, c_compose_condition, blur_sigma=blur_sigma)
                    loss_Gmain = torch.nn.functional.softplus(-gen_logits)
                    training_stats.report('Loss/scores/fake', gen_logits)
                    training_stats.report('Loss/signs/fake', gen_logits.sign())
                    training_stats.report('Loss/G/loss', loss_Gmain)
                else:
                    loss_Gmain = None

            with torch.autograd.profiler.record_function('Gmain_backward'):
                loss_G = loss_recon.mean()
                if loss_Gmain is not None:
                    loss_G += loss_Gmain.mean()*self.gmain
                loss_G.mul(gain).backward()

        # # Density Regularization
        if phase in ['Greg', 'Gboth'] and self.G.rendering_kwargs.get('density_reg', 0) > 0 and self.G.rendering_kwargs['reg_type'] == 'l1':

            initial_coordinates = torch.rand((real_c_recon.shape[0], 1000, 3), device=real_c_recon.device) * 2 - 1
            perturbed_coordinates = initial_coordinates + torch.randn_like(initial_coordinates) * self.G.rendering_kwargs['density_reg_p_dist'] * self.G.rendering_kwargs['box_warp']

            all_coordinates = torch.cat([initial_coordinates, perturbed_coordinates], dim=1)

            motion_scale = torch.ones([real_img_app.shape[0],1,1], device=real_img_app.device)*motion_scale
            if swapping_prob is not None:
                real_img_app_swapped = real_img_mot
                prob = torch.rand((real_img_app.shape[0], 1), device=real_img_app.device)
                real_img_app_conditioning = torch.where(prob.reshape(real_img_app.shape[0],1,1,1) < swapping_prob, real_img_app_swapped, real_img_app)
                motion_scale_conditioning = torch.where(prob.reshape(real_img_app.shape[0],1,1) < swapping_prob, torch.zeros_like(motion_scale), motion_scale)
                motions_app_conditioning = torch.where(prob < swapping_prob, motions, motions_app)
            else:
                real_img_app_conditioning = real_img_app
                motion_scale_conditioning = motion_scale
                motions_app_conditioning = motions_app

            if half_static:
                num_static = real_img_app.shape[0]//2
                if swapping_prob is None:
                    motion_scale_conditioning = torch.cat([motion_scale[:num_static],motion_scale[num_static:]*0],dim=0)
                else:
                    prob = torch.rand((num_static, 1), device=real_img_app.device)
                    motion_scale_static = torch.where(prob.reshape(num_static,1,1) < 1 - swapping_prob, torch.zeros_like(motion_scale[num_static:]), motion_scale[num_static:])
                    motion_scale_conditioning = torch.cat([motion_scale_conditioning[:num_static],motion_scale_static],dim=0)

            out = self.G.sample_mixed(real_img_app_conditioning, real_img_mot, motions_app_conditioning, motions, all_coordinates, torch.randn_like(all_coordinates), shape_params_app, exp_params_mot, pose_params_mot, eye_pose_params_mot, motion_scale=motion_scale_conditioning)
            if isinstance(out, tuple):
                TVloss = 0
                for out_ in out:
                    sigma = out_['sigma'][:,:initial_coordinates.shape[1]*2]
                    sigma_initial = sigma[:, :sigma.shape[1]//2]
                    sigma_perturbed = sigma[:, sigma.shape[1]//2:]
                    TVloss += torch.nn.functional.l1_loss(sigma_initial, sigma_perturbed) * self.G.rendering_kwargs['density_reg'] / len(out)
                training_stats.report('Loss/G/TVloss', TVloss)
            else:
                sigma = out['sigma'][:,:initial_coordinates.shape[1]*2]
                sigma_initial = sigma[:, :sigma.shape[1]//2]
                sigma_perturbed = sigma[:, sigma.shape[1]//2:]

                TVloss = torch.nn.functional.l1_loss(sigma_initial, sigma_perturbed) * self.G.rendering_kwargs['density_reg']
                training_stats.report('Loss/G/TVloss', TVloss)
            
            (TVloss).mul(gain).backward()

        # Dmain: Minimize logits for generated images.
        if cur_nimg >= self.discrimination_kimg * 1e3 and self.use_D:
            loss_Dgen = 0
            if phase in ['Dmain', 'Dboth']:
                with torch.autograd.profiler.record_function('Dgen_forward'):
                    gen_img = self.run_G(real_img_app, real_img_mot, shape_params_app, exp_params_mot, pose_params_mot, eye_pose_params_mot, motions_app, motions, real_c_recon, patch_scale=self.patch_scale, run_full=True, neural_rendering_resolution=neural_rendering_resolution, neural_rendering_resolution_patch=neural_rendering_resolution_patch, motion_scale=motion_scale, swapping_prob=swapping_prob, half_static=half_static)

                    c_compose_condition = real_c_compose_recon.clone()          
                    
                    gen_logits = self.run_D(gen_img, c_compose_condition, blur_sigma=blur_sigma, update_emas=True)
                    training_stats.report('Loss/scores/fake', gen_logits)
                    training_stats.report('Loss/signs/fake', gen_logits.sign())
                    loss_Dgen = torch.nn.functional.softplus(gen_logits)
                with torch.autograd.profiler.record_function('Dgen_backward'):
                    loss_Dgen.mean().mul(gain).backward()

            # Dmain: Maximize logits for real images.
            # Dr1: Apply R1 regularization.
            if phase in ['Dmain', 'Dreg', 'Dboth']:
                name = 'Dreal' if phase == 'Dmain' else 'Dr1' if phase == 'Dreg' else 'Dreal_Dr1'
                with torch.autograd.profiler.record_function(name + '_forward'):

                    real_img_tmp_image = real_img['image_sr'].detach().requires_grad_(phase in ['Dreg', 'Dboth'])
                    real_uv_tmp_image = real_img['uv'].detach().requires_grad_(phase in ['Dreg', 'Dboth'])
                    real_img_tmp_image_raw = real_img['image'].detach().requires_grad_(phase in ['Dreg', 'Dboth'])
                    real_seg_tmp_image = real_img['seg'].detach().requires_grad_(phase in ['Dreg', 'Dboth'])
                    real_img_tmp = {'image_sr': real_img_tmp_image, 'image': real_img_tmp_image_raw, 'uv': real_uv_tmp_image, 'seg':real_seg_tmp_image}

                    c_compose_condition = real_c_compose_recon.clone()

                    real_logits = self.run_D(real_img_tmp, c_compose_condition, blur_sigma=blur_sigma)
                    training_stats.report('Loss/scores/real', real_logits)
                    training_stats.report('Loss/signs/real', real_logits.sign())

                    loss_Dreal = 0
                    if phase in ['Dmain', 'Dboth']:
                        loss_Dreal = torch.nn.functional.softplus(-real_logits)
                        training_stats.report('Loss/D/loss', loss_Dgen + loss_Dreal)

                    loss_Dr1 = 0
                    if phase in ['Dreg', 'Dboth']:
                        if self.dual_discrimination:
                            with torch.autograd.profiler.record_function('r1_grads'), conv2d_gradfix.no_weight_gradients():
                                if self.G.has_superresolution:
                                    if self.D.has_uv and self.D.has_seg:
                                        r1_grads = torch.autograd.grad(outputs=[real_logits.sum()], inputs=[real_img_tmp['image_sr'],real_img_tmp['image'], real_img_tmp['uv'], real_img_tmp['seg']], create_graph=True, only_inputs=True)
                                        r1_grads_uv = r1_grads[2]
                                        r1_grads_seg = r1_grads[3]
                                    elif self.D.has_uv:
                                        r1_grads = torch.autograd.grad(outputs=[real_logits.sum()], inputs=[real_img_tmp['image_sr'],real_img_tmp['image'], real_img_tmp['uv']], create_graph=True, only_inputs=True)
                                        r1_grads_uv = r1_grads[2]
                                        r1_grads_seg = torch.zeros_like(r1_grads_uv)
                                    elif self.D.has_seg:
                                        r1_grads = torch.autograd.grad(outputs=[real_logits.sum()], inputs=[real_img_tmp['image_sr'],real_img_tmp['image'], real_img_tmp['seg']], create_graph=True, only_inputs=True)
                                        r1_grads_seg = r1_grads[2]
                                        r1_grads_uv = torch.zeros_like(r1_grads_seg)
                                    else:
                                        r1_grads = torch.autograd.grad(outputs=[real_logits.sum()], inputs=[real_img_tmp['image_sr'],real_img_tmp['image']], create_graph=True, only_inputs=True)
                                        r1_grads_uv = torch.zeros_like(r1_grads[0])
                                        r1_grads_seg = torch.zeros_like(r1_grads[0])
                                    r1_grads_image = r1_grads[0]
                                    r1_grads_image_raw = r1_grads[1]
                                    
                            r1_penalty = r1_grads_image.square().sum([1,2,3]) + r1_grads_image_raw.square().sum([1,2,3])
                            r1_penalty_uv =  r1_grads_uv.square().sum([1,2,3])
                            r1_penalty_seg = r1_grads_seg.square().sum([1,2,3])
                        else: # single discrimination
                            with torch.autograd.profiler.record_function('r1_grads'), conv2d_gradfix.no_weight_gradients():
                                if self.G.has_superresolution:
                                    r1_grads = torch.autograd.grad(outputs=[real_logits.sum()], inputs=[real_img_tmp['image_sr']], create_graph=True, only_inputs=True)
                                else:
                                    r1_grads = torch.autograd.grad(outputs=[real_logits.sum()], inputs=[real_img_tmp['image']], create_graph=True, only_inputs=True)
                                r1_grads_image = r1_grads[0]
                            r1_penalty = r1_grads_image.square().sum([1,2,3])
                            r1_penalty_uv = torch.zeros_like(r1_penalty)
                        loss_Dr1 = r1_penalty * (r1_gamma / 2) + r1_penalty_uv * (r1_gamma_uv / 2) + r1_penalty_seg * (r1_gamma_seg / 2)
                        training_stats.report('Loss/r1_penalty', r1_penalty)
                        training_stats.report('Loss/D/reg', loss_Dr1)

                with torch.autograd.profiler.record_function(name + '_backward'):
                    (loss_Dreal + loss_Dr1).mean().mul(gain).backward()



