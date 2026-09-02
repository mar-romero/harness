import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from request_normalizer import normalize_task, validate
from task_router import route
class RequestNormalizerTests(unittest.TestCase):
    def test_spanish_auth_production_equivalent_r3(self):
        es={'id':'T-es','description':'Cambiar autenticación en producción usando config/auth.json','risk_factors':{'touches_auth':True,'touches_production':True}}
        n=normalize_task(es,english='Change authentication in production using config/auth.json',language='es',back_translation='Cambiar autenticación en producción usando config/auth.json',equivalence='EXACT_INTENT')
        en={'id':'T-en','description':'Change authentication in production using config/auth.json','risk_factors':{'touches_auth':True,'touches_production':True}}
        self.assertEqual(route(n)['risk'],'R3'); self.assertEqual(route(en)['risk'],'R3')
    def test_lost_path_fails(self):
        r=validate('Cambiar config/auth.json a 30 segundos','Change it to 30 seconds','es','Cambiarlo a 30 segundos','EXACT_INTENT')
        self.assertFalse(r['ok'])
    def test_non_english_requires_backtranslation(self):
        r=validate('No borrar users.db','Do not delete users.db','es',None,'EXACT_INTENT'); self.assertFalse(r['ok'])
if __name__=='__main__': unittest.main()
