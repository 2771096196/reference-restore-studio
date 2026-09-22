import unittest
import numpy as np
from engine import brush_coverage, feather_inside


class BrushTests(unittest.TestCase):
    def test_flow_accumulates_within_opacity_cap(self):
        shape=(128,160); path=[(30,64),(125,64)]
        low=brush_coverage(path,shape,12,.5,.5,.1)
        high=brush_coverage(path,shape,12,.5,.5,1)
        repeated=brush_coverage(path+path[::-1]+path,shape,12,.5,.5,.1)
        self.assertLess(low.max(),high.max())
        self.assertGreater(repeated.max(),low.max())
        self.assertLessEqual(repeated.max(),.500001)

    def test_event_density_does_not_change_coverage(self):
        a=brush_coverage([(30,64),(125,64)],(128,160),12,.5,.5,.1)
        b=brush_coverage([(x,64) for x in range(30,126)],(128,160),12,.5,.5,.1)
        np.testing.assert_allclose(a,b,atol=1e-5)

    def test_zero_opacity_or_flow_is_noop(self):
        for opacity,flow in [(0,1),(1,0)]:
            self.assertFalse(brush_coverage([(15,15)],(30,30),8,.5,opacity,flow).any())

    def test_feather_does_not_bleed_outside_selection(self):
        mask=np.zeros((40,40),np.float32);mask[10:30,10:30]=1
        result=feather_inside(mask,5)
        self.assertTrue(np.all(result[mask==0]==0))
        self.assertGreater(result[20,20],result[10,10])

    def test_opacity_scales_one_stroke_linearly(self):
        full=brush_coverage([(32.3,32.7)],(70,70),20,.5,1,1)
        for opacity in (.1,.3,.6):
            np.testing.assert_allclose(brush_coverage([(32.3,32.7)],(70,70),20,.5,opacity,1),full*opacity,atol=1e-7)

    def test_full_flow_does_not_harden_a_soft_tip_by_overlapping(self):
        path=[(25,40),(90,40)]
        one=brush_coverage(path,(90,120),20,0,1,1)
        repeated=brush_coverage(path+path[::-1]+path,(90,120),20,0,1,1)
        self.assertLess(one[55,50],.3)
        self.assertLess(repeated[55,50],.3)
        self.assertGreater(one[55,50],0)

    def test_fractional_brush_positions_are_not_rounded_away(self):
        a=brush_coverage([(15.1,15)],(32,32),.7,1,1,1)
        b=brush_coverage([(15.4,15)],(32,32),.7,1,1,1)
        self.assertFalse(np.array_equal(a,b))

    def test_same_stroke_low_flow_builds_up_to_opacity_cap(self):
        path=[(25,40),(90,40)]
        amounts=[]
        for passes in (1,3,9):
            coords=path[:]
            for i in range(passes-1): coords+=path[::-1] if i%2==0 else path
            coverage=brush_coverage(coords,(90,120),15,.5,.6,.05)
            amounts.append(float(coverage[40,50]))
            self.assertLessEqual(float(coverage.max()),.600001)
        self.assertLess(amounts[0],amounts[1]);self.assertLess(amounts[1],amounts[2])


if __name__=='__main__':unittest.main()
