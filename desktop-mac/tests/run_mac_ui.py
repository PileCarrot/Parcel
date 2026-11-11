"""macOS Tcl/Tk supports one application root per process reliably."""
import os,subprocess,sys,unittest
from pathlib import Path
os.chdir(Path(__file__).resolve().parents[1])
sys.path.insert(0,str(Path.cwd()));sys.path.insert(0,'tests')
from test_desktop import DesktopTests
names=unittest.defaultTestLoader.getTestCaseNames(DesktopTests)
failures=[]
for name in names:
    code = "import sys,unittest,faulthandler;sys.path.insert(0,'tests');faulthandler.dump_traceback_later(18,exit=True);from test_desktop import DesktopTests;result=unittest.TextTestRunner().run(unittest.TestSuite([DesktopTests("+repr(name)+")]));faulthandler.cancel_dump_traceback_later();sys.exit(not result.wasSuccessful())"
    try:
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=22)
        if result.returncode:
            failures.append(name);print('FAIL',name,result.stderr[-2200:],flush=True)
        else: print('PASS',name,flush=True)
    except subprocess.TimeoutExpired:
        failures.append(name);print('TIMEOUT',name,flush=True)
print(f'{len(names)-len(failures)}/{len(names)} UI tests passed',flush=True)
raise SystemExit(bool(failures))
