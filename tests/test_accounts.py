import base64,json,pathlib,tempfile,unittest,sys
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from api import Api
from storage import account_options

class AccountPickerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='wxexport_accounts_');self.root=pathlib.Path(self.temp.name);self.accounts=self.root/'xwechat_files';self.data=self.root/'appdata';self.accounts.mkdir();self.data.mkdir()
        for name in ['wxid_a_1111','wxid_b_2222','wxid_c_3333']:(self.accounts/name/'db_storage').mkdir(parents=True)
        for name in ['all_users','Backup','WMPF']:(self.accounts/name).mkdir()
        avatar='data:image/png;base64,'+base64.b64encode((ROOT/'assets/emoji/捂脸.png').read_bytes()).decode()
        profiles={str(self.accounts/'wxid_a_1111'):{'display_name':'账号甲昵称','avatar':avatar},str(self.accounts/'wxid_b_2222'):{'display_name':'账号乙昵称','avatar':avatar}}
        (self.data/'account_profiles.json').write_text(json.dumps(profiles,ensure_ascii=False),encoding='utf-8');self.api=Api(self.data,ROOT/'assets')
    def tearDown(self):self.temp.cleanup()
    def test_scoped_accounts_and_friendly_labels(self):
        result=self.api.set_account_root(str(self.accounts));self.assertEqual(len(result['accounts']),3)
        self.assertEqual([a['name'] for a in result['accounts']],['账号甲昵称','账号乙昵称','账号 3'])
        self.assertTrue(result['accounts'][0]['avatar']);self.assertTrue(result['accounts'][2]['profile_pending'])
    def test_root_and_selection_persist(self):
        self.api.set_account_root(str(self.accounts));selected=str(self.accounts/'wxid_b_2222');self.api.select_account(selected)
        result=Api(self.data,ROOT/'assets').boot();self.assertEqual(result['root'],str(self.accounts));self.assertEqual(result['selected'],selected)
    def test_invalid_root_does_not_change_selection(self):
        self.api.set_account_root(str(self.accounts));self.assertFalse(self.api.set_account_root(str(self.accounts/'wxid_a_1111'))['ok'])
        empty=self.root/'empty';empty.mkdir();self.assertFalse(self.api.set_account_root(str(empty))['ok']);self.assertEqual(self.api.account_list()['root'],str(self.accounts))
        self.assertFalse(self.api.select_account(str(self.root/'outside'))['ok'])
    def test_busy_task_cannot_change_account(self):
        self.api.set_account_root(str(self.accounts));self.api._job['running']=True
        self.assertFalse(self.api.select_account(str(self.accounts/'wxid_b_2222'))['ok']);self.assertFalse(self.api.set_account_root(str(self.accounts))['ok'])

if __name__=='__main__':unittest.main()
