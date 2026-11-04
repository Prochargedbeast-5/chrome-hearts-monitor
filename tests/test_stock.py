import json, unittest
from datetime import datetime
from zoneinfo import ZoneInfo
import stock, persistence, run_monitor, monitor
import tempfile, os
from pathlib import Path
from unittest.mock import patch, MagicMock

class StockTests(unittest.TestCase):
    def page(self, status):
        return '<script type="application/ld+json">'+json.dumps({'@type':'Product','offers':{'availability':'https://schema.org/'+status}})+'</script>'
    def test_explicit_status_only(self):
        self.assertIs(stock.availability(self.page('InStock')), True)
        self.assertIs(stock.availability(self.page('OutOfStock')), False)
        self.assertIsNone(stock.availability(self.page('PreOrder')))
        self.assertIsNone(stock.availability('sold out <button>Add to cart</button>'))
        self.assertIsNone(stock.availability('<script type="application/ld+json">broken</script>'))
    def test_baseline_unknown_and_repeat_restocks(self):
        state, alerts = stock.update({}, {'u':True}, {'u':'Item'}, '1')
        self.assertFalse(alerts)
        state, _ = stock.update(state, {'u':False}, {}, '2')
        same, alerts = stock.update(state, {'u':None}, {}, '3')
        self.assertEqual(state, same); self.assertFalse(alerts)
        state, first = stock.update(same, {'u':True}, {}, '4')
        self.assertEqual(len(first),1)
        state, alerts = stock.update(state, {'u':True}, {}, '5')
        self.assertFalse(alerts)
        state, _ = stock.update(state, {'u':False}, {}, '6')
        state, second = stock.update(state, {'u':True}, {}, '7')
        self.assertEqual(len(second),1); self.assertNotEqual(first,second)
    def test_merge_keeps_newest_stock_and_pending(self):
        older={'stock':{'u':{'available':False,'observed_at':'1'}},'pending':{'restock':'message'}}
        newer={'stock':{'u':{'available':True,'observed_at':'2'}}}
        for a,b in [(older,newer),(newer,older)]:
            result=persistence.merge_state(a,b)
            self.assertTrue(result['stock']['u']['available'])
            self.assertEqual(result['pending'],{'restock':'message'})
    def test_window_in_winter_and_summer(self):
        for month in [1,7]:
            for hour,minute,active in [(9,29,False),(9,30,True),(10,0,True),(21,59,True),(22,0,False)]:
                now=datetime(2026,month,4,hour,minute,tzinfo=ZoneInfo('America/Chicago'))
                self.assertEqual(run_monitor.seconds_left(now)>0,active)

    def test_restock_saved_and_delivered_once(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(monitor, 'STATE', Path(tmp)/'state.json'), patch.object(monitor, 'save_state'), patch.dict(os.environ, {'DISCORD_WEBHOOK_URL':'https://discord.com/api/webhooks/1/test'}), patch('sys.argv',['monitor.py']):
            persistence.atomic_write(monitor.STATE, {'categories':['cat'], 'products':{'u':'Item'}, 'stock':{'u':{'available':False,'observed_at':'1'}}, 'pending':{},'delivered':[]})
            page=MagicMock(); page.categories.return_value={'cat'}; page.products.return_value={'u':'Item'}
            with patch.object(monitor,'get_page',return_value=page), patch.object(monitor.stock,'collect',return_value={'u':True}), patch.object(monitor,'notify') as send:
                monitor.main(); monitor.main()
                self.assertEqual(send.call_count,1)
                self.assertIn('Restock detected',send.call_args.args[1])
                self.assertFalse(json.loads(monitor.STATE.read_text())['pending'])
