#
# Copyright (C) 2023, Inria
# GRAPHDECO research group, https://team.inria.fr/graphdeco
# All rights reserved.
#
# This software is free for non-commercial, research and evaluation use
# under the terms of the LICENSE.md file.
#
# For inquiries contact  george.drettakis@inria.fr
#

import torch
import math
from diff_gaussian_rasterization import (
    GaussianRasterizationSettings,
    GaussianRasterizer,
)
from gaussiansplatting.utils.sh_utils import eval_sh


def camera2rasterizer(viewpoint_camera, bg_color: torch.Tensor, sh_degree: int = 0):
    tanfovx = math.tan(viewpoint_camera.FoVx * 0.5)
    tanfovy = math.tan(viewpoint_camera.FoVy * 0.5)

    raster_settings = GaussianRasterizationSettings(
        image_height=int(viewpoint_camera.image_height),
        image_width=int(viewpoint_camera.image_width),
        tanfovx=tanfovx,
        tanfovy=tanfovy,
        bg=bg_color,
        scale_modifier=1.0,
        viewmatrix=viewpoint_camera.world_view_transform,
        projmatrix=viewpoint_camera.full_proj_transform,
        sh_degree=sh_degree,
        campos=viewpoint_camera.camera_center,
        prefiltered=False,
        debug=False,
    )

    rasterizer = GaussianRasterizer(raster_settings=raster_settings)

    return rasterizer


def render(
    viewpoint_camera,
    pc,
    pipe,
    bg_color: torch.Tensor,
    scaling_modifier=1.0,
    override_color=None,
    filter_switch=False,
    filter_rectangle=None,
    masks=None,
):
    """
    Render the scene.

    Background tensor (bg_color) must be on GPU!
    """

    # Create zero tensor. We will use it to make pytorch return gradients of the 2D (screen-space) means
    screenspace_points = (
        torch.zeros_like(
            pc.get_xyz, dtype=pc.get_xyz.dtype, requires_grad=True, device="cuda"
        )
        + 0
    )
    try:
        screenspace_points.retain_grad()
    except:
        pass

    # Set up rasterization configuration
    tanfovx = math.tan(viewpoint_camera.FoVx * 0.5)
    tanfovy = math.tan(viewpoint_camera.FoVy * 0.5)

    raster_settings = GaussianRasterizationSettings(
        image_height=int(viewpoint_camera.image_height),
        image_width=int(viewpoint_camera.image_width),
        tanfovx=tanfovx,
        tanfovy=tanfovy,
        bg=bg_color,
        scale_modifier=scaling_modifier,
        viewmatrix=viewpoint_camera.world_view_transform,
        projmatrix=viewpoint_camera.full_proj_transform,
        sh_degree=pc.active_sh_degree,
        campos=viewpoint_camera.camera_center,
        prefiltered=False,
        debug=False,
    )

    rasterizer = GaussianRasterizer(raster_settings=raster_settings)

    means3D = pc.get_xyz
    means2D = screenspace_points
    opacity = pc.get_opacity

    # If precomputed 3d covariance is provided, use it. If not, then it will be computed from
    # scaling / rotation by the rasterizer.
    scales = None
    rotations = None
    cov3D_precomp = None
    if pipe.compute_cov3D_python:
        cov3D_precomp = pc.get_covariance(scaling_modifier)
    else:
        scales = pc.get_scaling
        rotations = pc.get_rotation

    # If precomputed colors are provided, use them. Otherwise, if it is desired to precompute colors
    # from SHs in Python, do it. If not, then SH -> RGB conversion will be done by rasterizer.
    shs = None
    colors_precomp = None
    if override_color is None:
        if pipe.convert_SHs_python:
            shs_view = pc.get_features.transpose(1, 2).view(
                -1, 3, (pc.max_sh_degree + 1) ** 2
            )
            dir_pp = pc.get_xyz - viewpoint_camera.camera_center.repeat(
                pc.get_features.shape[0], 1
            )
            dir_pp_normalized = dir_pp / dir_pp.norm(dim=1, keepdim=True)
            sh2rgb = eval_sh(pc.active_sh_degree, shs_view, dir_pp_normalized)
            colors_precomp = torch.clamp_min(sh2rgb + 0.5, 0.0)
        else:
            shs = pc.get_features

        shs = shs.float()
    else:
        colors_precomp = override_color

    # 如果想让某些高斯不显示，在这里利用mask取出来然后输入进去
    # means3D,shs,opacities,scales,rotations, 渲染的时候switch设置为false， 只有投影的时候才设置为true
    
    
    # Rasterize visible Gaussians to image, obtain their radii (on screen).
    # import pdb; pdb.set_trace()  在这里预设了所有高斯的颜色colors_precomp，将shs设置为None，用colors做渲染
    rendered_image, radii, depth = rasterizer(
        means3D=means3D.float(),
        means2D=means2D.float(),
        shs=shs,
        colors_precomp=colors_precomp,
        opacities=opacity.float(),
        scales=scales.float(),
        rotations=rotations.float(),
        cov3D_precomp=cov3D_precomp,
        filter_switch=filter_switch,
        filter_rectangle=filter_rectangle,
        masks=masks,
    )

    # Those Gaussians that were frustum culled or had a radius of 0 were not visible.
    # They will be excluded from value updates used in the splitting criteria.
    return {
        "render": rendered_image,
        "viewspace_points": screenspace_points,
        "visibility_filter": radii > 0,
        "radii": radii,
        "depth_3dgs": depth,
    }


# from gaussiansplatting.scene.gaussian_model import GaussianModel


def point_cloud_render(
    viewpoint_camera,
    xyz,
    pipe,
    bg_color: torch.Tensor,
    scaling_modifier=1.0,
    override_color=None,
):
    screenspace_points = (
        torch.zeros_like(xyz, dtype=xyz.dtype, requires_grad=True, device="cuda") + 0
    )
    try:
        screenspace_points.retain_grad()
    except:
        pass

    # Set up rasterization configuration
    tanfovx = math.tan(viewpoint_camera.FoVx * 0.5)
    tanfovy = math.tan(viewpoint_camera.FoVy * 0.5)

    raster_settings = GaussianRasterizationSettings(
        image_height=int(viewpoint_camera.image_height),
        image_width=int(viewpoint_camera.image_width),
        tanfovx=tanfovx,
        tanfovy=tanfovy,
        bg=bg_color,
        scale_modifier=scaling_modifier,
        viewmatrix=viewpoint_camera.world_view_transform,
        projmatrix=viewpoint_camera.full_proj_transform,
        sh_degree=0,
        campos=viewpoint_camera.camera_center,
        prefiltered=False,
        debug=False,
    )

    rasterizer = GaussianRasterizer(raster_settings=raster_settings)

    means3D = xyz
    means2D = screenspace_points
    opacity = torch.ones_like(xyz[..., 0:1])

    # If precomputed 3d covariance is provided, use it. If not, then it will be computed from
    # scaling / rotation by the rasterizer.
    scales = None
    rotations = None
    cov3D_precomp = None
    scales = torch.ones_like(xyz) * 0.005
    rotations = torch.zeros([xyz.shape[0], 4], dtype=xyz.dtype, device=xyz.device)
    rotations[..., 0] = 1.0

    # If precomputed colors are provided, use them. Otherwise, if it is desired to precompute colors
    # from SHs in Python, do it. If not, then SH -> RGB conversion will be done by rasterizer.
    shs = None
    colors_precomp = None
    # if override_color is None:
    #     if pipe.convert_SHs_python:
    #         shs_view = pc.get_features.transpose(1, 2).view(
    #             -1, 3, (pc.max_sh_degree + 1) ** 2
    #         )
    #         dir_pp = pc.get_xyz - viewpoint_camera.camera_center.repeat(
    #             pc.get_features.shape[0], 1
    #         )
    #         dir_pp_normalized = dir_pp / dir_pp.norm(dim=1, keepdim=True)
    #         sh2rgb = eval_sh(pc.active_sh_degree, shs_view, dir_pp_normalized)
    #         colors_precomp = torch.clamp_min(sh2rgb + 0.5, 0.0)
    #     else:
    #         shs = pc.get_features

    #     shs = shs.float()
    # else:
    #     colors_precomp = override_color
    colors_precomp = torch.ones_like(xyz[..., 0:1]).repeat(1, 3)

    # Rasterize visible Gaussians to image, obtain their radii (on screen).
    # import pdb; pdb.set_trace()
    rendered_image, radii, depth = rasterizer(
        means3D=means3D.float(),
        means2D=means2D.float(),
        shs=shs,
        colors_precomp=colors_precomp,
        opacities=opacity.float(),
        scales=scales.float(),
        rotations=rotations.float(),
        cov3D_precomp=cov3D_precomp,
        
    )

    # Those Gaussians that were frustum culled or had a radius of 0 were not visible.
    # They will be excluded from value updates used in the splitting criteria.
    return {
        "render": rendered_image,
        "viewspace_points": screenspace_points,
        "visibility_filter": radii > 0,
        "radii": radii,
        "depth_3dgs": depth,
    }
