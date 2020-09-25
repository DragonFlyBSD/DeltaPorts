--- ortools/util/fp_utils.h.intermediate	2020-09-24 22:30:12 UTC
+++ ortools/util/fp_utils.h
@@ -83,7 +83,7 @@ class ScopedFloatingPointEnv {
     excepts &= FE_ALL_EXCEPT;
 #ifdef __APPLE__
     fenv_.__control &= ~excepts;
-#elif defined(__FreeBSD__)
+#elif defined(__FreeBSD__) || defined(__DragonFly__)
     //fesetexceptflag(&fenv_, excepts);
 #else  // Linux
     fenv_.__control_word &= ~excepts;
