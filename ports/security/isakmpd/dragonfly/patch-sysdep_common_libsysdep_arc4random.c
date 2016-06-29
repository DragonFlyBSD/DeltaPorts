--- sysdep/common/libsysdep/arc4random.c.orig	2004-12-07 22:07:05.000000000 +0200
+++ sysdep/common/libsysdep/arc4random.c
@@ -61,7 +61,11 @@ static inline void
 arc4_addrandom(as, dat, datlen)
 	struct arc4_stream *as;
 	u_char *dat;
+#ifdef __DragonFly__
+	size_t  datlen;
+#else
 	int     datlen;
+#endif
 {
 	int     n;
 	u_int8_t si;
@@ -140,7 +144,11 @@ arc4random_stir()
 void
 arc4random_addrandom(dat, datlen)
 	u_char *dat;
+#ifdef __DragonFly__
+	size_t  datlen;
+#else
 	int     datlen;
+#endif
 {
 	if (!rs_initialized)
 		arc4random_stir();
