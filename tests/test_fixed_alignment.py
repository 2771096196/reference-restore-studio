import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from engine import Project, write_image


class FixedAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.p=Project(self.directory.name)
        p=self.p
        self.reference=np.full((16,20,3),(90,140,210),np.uint8)
        image=Path(self.directory.name)/'image.png'
        write_image(image,self.reference)
        p.meta=dict(width=20,height=16,pw=20,ph=16,frames=2,image=str(image),
                    matrix=[[1,0,0],[0,1,0]],image_width=20,image_height=16)
        p.restore=np.zeros((16,20),np.uint8)
        p.restore[4:12,5:15]=128  # Intentionally translucent paint.
        p.restore[3,5:15]=1  # Very soft edge must be frozen too.
        p.protect=np.zeros_like(p.restore)
        p.valid=np.full_like(p.restore,255)
        p.score=np.zeros_like(p.restore,dtype=np.float32)
        p.reference=self.reference.copy();p.ref_small=self.reference.copy()
        p.back=np.zeros((2,16,20,2),np.float32);p.back[1,:,:,0]=2
        p.forward=-p.back
        p.settings.update(auto=False,face=False,alignment_mode='fixed',alignment_frame=0,strength=.6,paint_blend='frozen')
        p.original_frame=Mock(return_value=np.full((16,20,3),30,np.uint8))

    def test_final_painted_rgb_is_identical_even_with_transparency(self):
        p=self.p;s=p.snapshot();size=(40,32)
        a=p.compose_target(0,np.full((16,20,3),30,np.uint8),s,size,self.reference)
        b=p.compose_target(1,np.full((16,20,3),220,np.uint8),s,size,self.reference)
        support,_=p.fixed_paint_patch(s,size)
        np.testing.assert_array_equal(a[support],b[support])
        self.assertTrue(np.any(a[~support]!=b[~support]))
        p.original_frame.assert_called_once_with(0)

    def test_preview_and_face_masks_are_fixed(self):
        p=self.p;p.settings.update(face=True);p.face_region=[.2,.2,.7,.8]
        s=p.snapshot()
        with patch.object(p,'rigid_face_matrix',side_effect=AssertionError('Must not track')):
            a=p.restoration_masks(0,s);b=p.restoration_masks(1,s)
            for x,y in zip(a,b):np.testing.assert_array_equal(x,y)
            x=p.compose(0,snapshot=s,frame=np.full((16,20,3),30,np.uint8))[0]
            y=p.compose(1,snapshot=s,frame=np.full((16,20,3),220,np.uint8))[0]
        support,_=p.fixed_paint_patch(s,(20,16))
        np.testing.assert_array_equal(x[support],y[support])

    def test_cache_updates_on_paint_and_black_erases_lock(self):
        p=self.p;s=p.snapshot()
        first=p.fixed_paint_patch(s,(20,16))
        p.protect[4:12,5:15]=255
        second=p.fixed_paint_patch(p.snapshot(),(20,16))
        self.assertTrue(first[0][5,6]);self.assertFalse(second[0][5,6])
        self.assertEqual(p.original_frame.call_count,2)
        p.settings['enabled']=False
        self.assertIsNone(p.fixed_paint_patch(p.snapshot(),(20,16)))

    def test_anchor_alignment_is_computed_once_and_reused(self):
        p=self.p;p.settings['alignment_frame']=1
        matrix=np.array([[1,0,2],[0,1,0]],np.float32)
        with patch('engine.read_image',return_value=self.reference), patch('engine.align_reference',return_value=(matrix,{'inliers':20,'method':'特征匹配 + 旋转/缩放对齐'})) as align:
            a=p.alignment_flow(0,p.settings)
            b=p.alignment_flow(1,p.settings)
            self.assertIs(a,b)
            np.testing.assert_allclose(a[:,:,0],-2)
            align.assert_called_once()

    def test_tracked_mode_keeps_existing_flow(self):
        p=self.p;p.settings['alignment_mode']='tracked'
        self.assertIsNone(p.fixed_paint_patch(p.snapshot(),(20,16)))
        np.testing.assert_array_equal(p.alignment_flow(1,p.settings),p.back[1])

    def test_small_canvas_and_export_use_same_compositor(self):
        p=self.p
        for mode in ('fixed','tracked'):
            p.settings['alignment_mode']=mode
            size=p.preview_size('quarter')
            preview=p.preview_sized(1,'composite','quarter')
            expected=p.render_export_frame(1,p.original_frame(1),{'width':size[0],'height':size[1]},p.snapshot(),self.reference)
            np.testing.assert_array_equal(preview,expected)

    def test_export_preview_matches_export_with_selected_model(self):
        p=self.p;s=p.snapshot()
        for model in ('realesrgan','realesrgan-animevideo'):
            options=dict(width=40,height=32,upscale=model,denoise=.7,device='cpu',tile=96)
            upscaler=Mock()
            upscaler.enhance.side_effect=lambda frame,*args: np.minimum(frame.astype(int)+20,255).astype(np.uint8)
            with patch('engine.RealESRGAN',return_value=upscaler) as factory:
                preview=p.export_preview(1,options,s)
                factory.assert_called_once_with(.7,'cpu',96,model=model)
                upscaler.close.assert_called_once()
            expected=p.render_export_frame(1,p.original_frame(1),options,s,self.reference,upscaler)
            np.testing.assert_array_equal(preview,expected)

    def test_mask_overlay_and_composite_share_exact_target_coverage(self):
        p=self.p
        p.original_frame.side_effect=lambda index: np.full((16,20,3),30+index*180,np.uint8)
        for mode in ('fixed','tracked'):
            for face in (False,True):
                p.settings.update(alignment_mode=mode,face=face)
                p.face_region=[.15,.1,.75,.85]
                for size in ((5,4),(20,16),(33,29)):
                    for index in (0,1):
                        with self.subTest(mode=mode,face=face,size=size,index=index):
                            coverage=p.composition_coverage(index,p.snapshot(),size)
                            mask=p.preview_at_size(index,'mask',size)[:,:,0]
                            comp=p.preview_at_size(index,'composite',size)
                            raw=p.preview_at_size(index,'video',size)
                            overlay=p.preview_at_size(index,'overlay',size)
                            np.testing.assert_array_equal(mask,np.ceil(coverage*255).astype(np.uint8))
                            np.testing.assert_array_equal(comp[mask==0],raw[mask==0])
                            tint=np.array([45,200,255],np.float32)
                            expected=(comp*(1-coverage[...,None]*.55)+tint*coverage[...,None]*.55).clip(0,255).astype(np.uint8)
                            np.testing.assert_array_equal(overlay,expected)
                            if mode=='fixed':
                                support=p.fixed_paint_support(p.snapshot(),size)
                                self.assertTrue(np.all(mask[support]==255))

    def test_nonzero_anchor_aligns_mask_and_fixed_pixels(self):
        p=self.p;p.settings['alignment_frame']=1
        matrix=np.array([[1,0,2],[0,1,1]],np.float32)
        with patch('engine.read_image',return_value=self.reference), patch('engine.align_reference',return_value=(matrix,{'inliers':20,'method':'特征匹配 + 旋转/缩放对齐'})):
            for size in ((20,16),(41,33)):
                a=p.preview_at_size(0,'mask',size)
                b=p.preview_at_size(1,'mask',size)
                np.testing.assert_array_equal(a,b)
                support,locked=p.fixed_paint_patch(p.snapshot(),size)
                composite=p.preview_at_size(1,'composite',size)
                self.assertTrue(np.all(a[:,:,0][support]==255))
                np.testing.assert_array_equal(composite[support],locked[support])

    def test_soft_blend_keeps_gray_edges_and_opaque_center_stable(self):
        p=self.p;p.settings.update(paint_blend='soft',strength=1)
        p.restore[6:10,8:12]=255
        p.original_frame.side_effect=lambda index: np.full((16,20,3),30+index*180,np.uint8)
        mask=p.preview_at_size(0,'mask',(20,16))[:,:,0]
        self.assertEqual(mask[5,6],128)
        self.assertEqual(mask[7,9],255)
        self.assertEqual(mask[3,6],1)
        a=p.preview_at_size(0,'composite',(20,16));b=p.preview_at_size(1,'composite',(20,16))
        np.testing.assert_array_equal(a[mask==255],b[mask==255])
        self.assertTrue(np.any(a[mask==128]!=b[mask==128]))

    def test_black_and_white_opacity_are_not_applied_twice(self):
        p=self.p;p.settings.update(paint_blend='soft',strength=1)
        p.save_edits=Mock()
        p.restore[:]=255;p.protect[:]=0
        p.stroke([[.5,.5]],.2,'protect',0,'reference',1,.5,1)
        alpha=p.composition_coverage(0,p.snapshot(),(20,16))
        self.assertAlmostEqual(float(alpha[8,10]),.5,delta=.005)
        p.restore[:]=0;p.protect[:]=255
        p.stroke([[.5,.5]],.2,'restore',0,'reference',1,.3,1)
        alpha=p.composition_coverage(0,p.snapshot(),(20,16))
        self.assertAlmostEqual(float(alpha[8,10]),.3,delta=.005)


if __name__=='__main__':unittest.main()
