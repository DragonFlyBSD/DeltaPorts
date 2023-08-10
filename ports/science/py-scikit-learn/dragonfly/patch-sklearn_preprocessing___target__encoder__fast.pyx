--- sklearn/preprocessing/_target_encoder_fast.pyx.orig	2023-06-29 18:14:12 UTC
+++ sklearn/preprocessing/_target_encoder_fast.pyx
@@ -1,8 +1,8 @@
-from libc.math cimport isnan
 from libcpp.vector cimport vector
 
 cimport numpy as cnp
 import numpy as np
+import cmath
 
 cnp.import_array()
 
@@ -154,7 +154,7 @@ def _fit_encoding_fast_auto_smooth(
                     (y_variance * counts[cat_idx] + sum_of_squared_diffs[cat_idx] /
                      counts[cat_idx])
                 )
-                if isnan(lambda_):
+                if cmath.isnan(lambda_):
                     # A nan can happen when:
                     # 1. counts[cat_idx] == 0
                     # 2. y_variance == 0 and sum_of_squared_diffs[cat_idx] == 0
