--- xf86atomic.h.orig	2017-04-11 16:09:06 UTC
+++ xf86atomic.h
@@ -111,7 +111,7 @@ static inline int atomic_add_unless(atom
 	c = atomic_read(v);
 	while (c != unless && (old = atomic_cmpxchg(v, c, c + add)) != c)
 		c = old;
-	return c == unless;
+	return c != unless;
 }
 
 #endif
