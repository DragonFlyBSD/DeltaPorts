--- yt/utilities/lib/platform_dep_math.hpp.orig
+++ yt/utilities/lib/platform_dep_math.hpp
@@ -11,7 +11,7 @@
 inline bool __isnormal(double x) {
     return isnormal(x);
 }
-#elif defined(__FreeBSD__)
+#elif defined(__FreeBSD__) || defined(__DragonFly__)
 #else
 #include <cmath>
 inline bool __isnormal(double x) {
