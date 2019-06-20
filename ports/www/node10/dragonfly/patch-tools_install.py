--- tools/install.py.orig	2019-03-05 15:16:33 UTC
+++ tools/install.py
@@ -148,7 +148,7 @@ def files(action):
   action(['deps/v8/tools/lldbinit'], 'share/doc/node/')
   action(['deps/v8/tools/lldb_commands.py'], 'share/doc/node/')
 
-  if 'freebsd' in sys.platform or 'openbsd' in sys.platform:
+  if 'freebsd' in sys.platform or 'openbsd' or 'dragonfly' in sys.platform:
     action(['doc/node.1'], 'man/man1/')
   else:
     action(['doc/node.1'], 'share/man/man1/')
