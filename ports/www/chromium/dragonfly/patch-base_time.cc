--- base/time.cc.orig	2013-07-04 03:39:53.000000000 +0000
+++ base/time.cc
@@ -96,7 +96,7 @@ time_t Time::ToTimeT() const {
 
 // static
 Time Time::FromDoubleT(double dt) {
-  if (dt == 0 || isnan(dt))
+  if (dt == 0 || std::isnan(dt))
     return Time();  // Preserve 0 so we can tell it doesn't exist.
   if (dt == std::numeric_limits<double>::max())
     return Max();
