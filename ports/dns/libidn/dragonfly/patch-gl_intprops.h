Poisoned? Add gcc80.

--- gl/intprops.h.orig	2016-07-20 15:49:30.000000000 +0000
+++ gl/intprops.h
@@ -223,7 +223,7 @@ verify (TYPE_MAXIMUM (long long int) ==
    : (max) >> (b) < (a))
 
 /* True if __builtin_add_overflow (A, B, P) works when P is null.  */
-#define _GL_HAS_BUILTIN_OVERFLOW_WITH_NULL (7 <= __GNUC__)
+#define _GL_HAS_BUILTIN_OVERFLOW_WITH_NULL (9 <= __GNUC__)
 
 /* The _GL*_OVERFLOW macros have the same restrictions as the
    *_RANGE_OVERFLOW macros, except that they do not assume that operands
