$env:Path += ";C:\MinGW\bin\"

$env:Path += ";C:\Program Files (x86)\Windows Kits\10\bin\x86\"
gcc --version
mingw32-make --version

echo "installing pywin32"
pip install pywin32

# build/install miniupnpc manually
echo "building miniupnpc"
tar zxf miniupnpc-1.9.tar.gz
cd miniupnpc-1.9
mingw32-make -f Makefile.mingw
echo "installing python binding for miniupnpc"
python setupmingw32.py build --compiler=mingw32
python setupmingw32.py install
cd ..\
Remove-Item -Recurse -Force miniupnpc-1.9
python -c "import miniupnpc; print 'can import miniupnpc from %s' % miniupnpc.__file__"

# copy requirements from lbry, but remove gmpy and miniupnpc (installed manually)
Get-Content ..\requirements.txt | Select-String -Pattern 'gmpy|miniupnpc' -NotMatch | Out-File requirements_base.txt
# add in gmpy wheel
Add-Content requirements.txt "./gmpy-1.17-cp27-none-win32.whl"

echo "set build type"
python set_build.py

echo "installing lbrynet requirements"
pip install -r requirements.txt
python -c "import gmpy; print 'can import gmpy from %s' % gmpy.__file__"
echo "installing lbrynet"
pip install ..\.
python -c "import lbrynet; print 'can import lbrynet from %s' % lbrynet.__file__"
python -c "import win32api; print 'can import win32api from %s' % lbrynet.__file__"

