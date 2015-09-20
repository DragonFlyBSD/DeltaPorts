--- src/VectorT.h.orig	2012-02-06 15:02:05.000000000 +0200
+++ src/VectorT.h
@@ -22,6 +22,7 @@
 #include <math.h>
 #include <algorithm>
 #include <iostream>
+#include <cstring>
 
 template<typename Type, int Size>
 class VectorT
