--- opennurbs_system_runtime.h.orig
+++ opennurbs_system_runtime.h
@@ -202,6 +202,16 @@
 #endif
 
 #if !defined(ON_BIG_ENDIAN) && !defined(ON_LITTLE_ENDIAN)
+/* DragonFly has no ON_RUNTIME_* branch above; detect byte order from
+ * compiler macros (no-op if already set by the FreeBSD block). */
+#if defined(__BIG_ENDIAN__)
+#define ON_BIG_ENDIAN
+#elif defined(__LITTLE_ENDIAN__)
+#define ON_LITTLE_ENDIAN
+#endif
+#endif
+
+#if !defined(ON_BIG_ENDIAN) && !defined(ON_LITTLE_ENDIAN)
 #error Exactly one of ON_LITTLE_ENDIAN or ON_BIG_ENDIAN should be defined.
 #endif
 
