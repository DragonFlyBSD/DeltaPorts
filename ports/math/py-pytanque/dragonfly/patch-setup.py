--- setup.py.intermediate	2021-12-24 02:25:31.000000000 +0000
+++ setup.py
@@ -3,11 +3,11 @@ import glob
 import platform
 import subprocess
 
-if platform.system() in ("Linux","Darwin","FreeBSD"):
+if platform.system() in ("Linux","Darwin","DragonFly","FreeBSD"):
     # This will work w/ GCC and clang
     compile_args = ['-std=c++14','-flto','-Dpetanque_STATIC']
     link_args = ['-flto']
-    if platform.system() == "Linux" or platform.system() == "FreeBSD":
+    if platform.system() == "Linux" or platform.system() == "DragonFly" or platform.system() == "FreeBSD":
         link_args = ["-Wl,--strip-all","-Wl,-gc-sections"]
 elif platform.system() == "Windows":
     compile_args = ['/Dpetanque_STATIC','/TP', '/EHsc']
