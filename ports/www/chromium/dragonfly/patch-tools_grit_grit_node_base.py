--- tools/grit/grit/node/base.py.intermediate	2014-01-05 13:16:12.322237000 +0000
+++ tools/grit/grit/node/base.py
@@ -460,9 +460,10 @@ class Node(object):
         'is_win': target_platform in ('cygwin', 'win32'),
         'is_android': target_platform == 'android',
         'is_ios': target_platform == 'ios',
-        'is_bsd': 'bsd' in target_platform,
+        'is_bsd': (target_platform.startswith('dragonfly') or 'bsd' in target_platform),
         'is_posix': (target_platform in ('darwin', 'linux2', 'linux3', 'sunos5',
                                          'android', 'ios')
+                    or target_platform.startswith('dragonfly')
                     or 'bsd' in target_platform),
         'pp_ifdef' : pp_ifdef,
         'pp_if' : pp_if,
