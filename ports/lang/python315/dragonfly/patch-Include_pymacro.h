--- Include/pymacro.h.orig
+++ Include/pymacro.h
@@ -25,7 +25,7 @@
 // behaviour. So only define if building as C, not C++ (if __cplusplus is
 // not defined), and only for C11-17.
 #if !defined(static_assert) && (defined(__GNUC__) || defined(__clang__)) \
-     && !defined(_Py_STDC_VERSION) \
+     && defined(_Py_STDC_VERSION) \
      && _Py_STDC_VERSION >= 201112L && _Py_STDC_VERSION <= 201710L
 #  define static_assert _Static_assert
 #endif
