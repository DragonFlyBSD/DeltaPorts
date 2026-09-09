diff --git third_party/node/node.py third_party/node/node.py
index a7912ad39045..13df5e1361e0 100755
--- third_party/node/node.py
+++ third_party/node/node.py
@@ -22,12 +22,17 @@ def GetBinaryPath():
     'Linux': ('linux', 'node-linux-x64', 'bin', 'node'),
     'OpenBSD': ('openbsd', 'node-openbsd', 'bin', 'node'),
     'FreeBSD': ('freebsd', 'node-freebsd', 'bin', 'node'),
+    'DragonFly': ('dragonfly', 'node-dragonfly', 'bin', 'node'),
     'Windows': ('win', 'node.exe'),
   }[platform.system()])
 
 
 def RunNode(cmd_parts, stdout=None):
-  cmd = [GetBinaryPath()] + cmd_parts
+  # XXX: fails with SIGSEGV because Chromium's build uses a WASM Rollup, and
+  # V8's TURBOFAN WASM optimizer on DragonFly hits a platform-specific
+  # bug/miscompile in the BitcastElider -> codegen path.
+  # chatgpt
+  cmd = [GetBinaryPath()] + ['--liftoff-only'] +  cmd_parts
   process = subprocess.Popen(
       cmd, cwd=os.getcwd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
       universal_newlines=True, encoding='utf-8')
