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


if __name__=='__main__':unittest.main()
