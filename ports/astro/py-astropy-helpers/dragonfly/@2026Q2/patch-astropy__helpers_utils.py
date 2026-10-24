--- astropy_helpers/utils.py.orig	2019-10-26 23:50:58 UTC
+++ astropy_helpers/utils.py
@@ -1,12 +1,20 @@
 # Licensed under a 3-clause BSD style license - see LICENSE.rst
 
 import contextlib
-import imp
 import os
 import sys
 import glob
 
 from importlib import machinery as import_machinery
+try:
+    from importlib import util as import_util
+except ImportError:
+    import_util = None
+try:
+    from importlib import reload as _reload
+except ImportError:
+    import imp as _imp
+    _reload = _imp.reload
 
 
 # Note: The following Warning subclasses are simply copies of the Warnings in
@@ -54,9 +62,8 @@ def get_numpy_include_path():
     import builtins
     if hasattr(builtins, '__NUMPY_SETUP__'):
         del builtins.__NUMPY_SETUP__
-    import imp
     import numpy
-    imp.reload(numpy)
+    _reload(numpy)
 
     try:
         numpy_include = numpy.get_include()
@@ -218,11 +225,25 @@ def import_file(filename, name=None):
         raise ImportError('Could not import file {0}'.format(filename))
 
     if import_machinery:
-        loader = import_machinery.SourceFileLoader(name, filename)
-        mod = loader.load_module()
+        try:
+            loader = import_machinery.SourceFileLoader(name, filename)
+            try:
+                spec = import_util.spec_from_loader(name, loader)
+                mod = import_util.module_from_spec(spec)
+                loader.exec_module(mod)
+            except (AttributeError, NameError):
+                # Very old importlib without spec API; fall back to
+                # the legacy load_module() entry point.
+                mod = loader.load_module()
+        except AttributeError:
+            loader = import_machinery.SourceFileLoader(name, filename)
+            mod = loader.load_module()
     else:
-        with open(filename, mode) as fd:
-            mod = imp.load_module(name, fd, filename, ('.py', mode, 1))
+        # import_machinery is always available on Python 3; this
+        # fallback avoids the removed stdlib 'imp' module entirely.
+        spec = import_util.spec_from_file_location(name, filename)
+        mod = import_util.module_from_spec(spec)
+        spec.loader.exec_module(mod)
 
     return mod
 
