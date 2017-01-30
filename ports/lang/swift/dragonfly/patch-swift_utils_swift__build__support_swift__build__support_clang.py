--- swift/utils/swift_build_support/swift_build_support/clang.py.orig	2016-05-03 19:56:30 UTC
+++ swift/utils/swift_build_support/swift_build_support/clang.py
@@ -73,6 +73,8 @@ def host_clang(xcrun_toolchain):
             return CompilerExecutable(cc=cc, cxx=cxx)
         else:
             return None
+    elif platform.system() == 'DragonFly':
+        return _first_clang(['38', '37'])
     elif platform.system() == 'FreeBSD':
         # See: https://github.com/apple/swift/pull/169
         # Building Swift from source requires a recent version of the Clang
