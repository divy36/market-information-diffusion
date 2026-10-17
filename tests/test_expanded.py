"""Guard against false precision, wrong table columns and missed co-releases."""
import math
import unittest
from datetime import datetime, timedelta, timezone
from market_diffusion.alignment import Config, QuoteIndex
from market_diffusion.data import Event, Quote
from market_diffusion.models import fit_response, response
from market_diffusion.bls import parse_release
from market_diffusion.news_controls import calendar_rows

class ExpandedTests(unittest.TestCase):
    def test_subsecond_causal_grid_and_exact_endpoints(self):
        t=datetime(2022,1,12,13,30,tzinfo=timezone.utc)
        e=Event('x','CPI',t,.1,0,t-timedelta(hours=1),'pp','fixture')
        quotes=[Quote('X',t-timedelta(seconds=1),1,1),Quote('X',t+timedelta(milliseconds=251),2,2)]
        points=QuoteIndex(quotes).align(e,Config(step_seconds=.25))
        bytime={p.tau:p for p in points}
        self.assertEqual(len(points),1441)
        self.assertEqual((points[0].tau,points[-1].tau),(-60,300))
        self.assertEqual(bytime[.25].mid,1)
        self.assertEqual(bytime[.5].mid,2)
        self.assertIsNone(bytime[2.5].mid)

    def test_invalid_fractional_steps(self):
        for step in [True,float('nan'),float('inf'),.0005,.251,-.1]:
            with self.assertRaises(ValueError): Config(step_seconds=step)

    def test_optimized_loss_matches_direct_grid_search(self):
        points=[(i/4,response(i/4,.35,2)+.01*math.sin(i)) for i in range(81)]
        fit=fit_response(points,20)
        expected=min((sum((v-response(t,j/20,k))**2 for t,v in points)/len(points),j/20,k)
                     for k in [.25*(8*20)**(i/100) for i in range(101)] for j in range(20))
        self.assertEqual((fit['jump'],fit['scale_seconds']),expected[1:])
        self.assertAlmostEqual(fit['rmse']**2,expected[0],places=13)

    def test_fixed_width_release_current_month_and_missing_rejected(self):
        text='''https://www.bls.gov/news.release/archives/cpi_01122022.htm
L1: embargoed until 8:30 a.m. (ET) January 12, 2022
L2: CONSUMER PRICE INDEX � DECEMBER 2021
L3: Table A. Percent changes in CPI for All Urban Consumers
L4: Seasonally adjusted changes from
L5: preceding month
L6: All items.................. .9 .5 .3 .4 .9 .8 .5 7.0
L7: All items less food and
L8: energy..................... .9 .3 .1 .2 .6 .5 .6 5.5
'''
        r=parse_release(text)
        self.assertEqual((r['headline_mom_sa'],r['core_mom_sa']),(.5,.6))
        with self.assertRaises(ValueError): parse_release(text.replace('.8 .5 7.0','.8 - 7.0'))

    def test_calendar_groups_cpi_but_retains_other_news_and_dates(self):
        t=datetime(2023,1,12,13,30,tzinfo=timezone.utc)
        rows=calendar_rows('''GLOBAL ECONOMICS US 01-12 08:30 CPI (m/m) Dec 0 .1 .2
US 01-12 08:30 CPI ex. Food & Energy (m/m) Dec .3 .3 .2
US 01-12 08:30 Initial Jobless Claims (000s) Jan 7 220 215 204
US 01-12 08:45 Fed speech
US 01-13 08:30 Retail sales''',t)
        self.assertEqual(len(rows),4)
        self.assertEqual([r['us_cpi_bundle'] for r in rows],[True,True,False,False])
        self.assertEqual(rows[-1]['offset_seconds'],900)

if __name__=='__main__': unittest.main()
