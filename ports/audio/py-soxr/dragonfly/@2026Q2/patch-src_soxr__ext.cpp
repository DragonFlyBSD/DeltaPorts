--- src/soxr_ext.cpp.orig	2022-11-09 12:37:21 UTC
+++ src/soxr_ext.cpp
@@ -96,7 +96,7 @@ class CSoxr { (public)
     }
 
     template <typename T>
-    auto process(
+    ndarray<nb::numpy, T> process(
             ndarray<const T, nb::ndim<2>, nb::c_contig, nb::device::cpu> x,
             bool last=false) {
         const unsigned channels = x.shape(1);
@@ -176,7 +176,7 @@ class CSoxr { (public)
 // soxr_oneshot() becomes much slower when input is long.
 // To avoid this, divide long input and process.
 template <typename T>
-auto csoxr_divide_proc(
+ndarray<nb::numpy, T> csoxr_divide_proc(
         double in_rate, double out_rate,
         ndarray<const T, nb::ndim<2>, nb::c_contig, nb::device::cpu> x,
         unsigned long quality) {
@@ -243,7 +243,7 @@ auto csoxr_divide_proc(
 
 // split channel memory I/O (e.g. Fortran order)
 template <typename T>
-auto csoxr_split_ch(
+ndarray<nb::numpy, T> csoxr_split_ch(
         double in_rate, double out_rate,
         ndarray<const T, nb::ndim<2>, nb::device::cpu> x,
         unsigned long quality) {
@@ -328,7 +328,7 @@ auto csoxr_split_ch(
 
 
 template <typename T>
-auto csoxr_oneshot(
+ndarray<nb::numpy, T> csoxr_oneshot(
         double in_rate, double out_rate,
         ndarray<const T, nb::ndim<2>, nb::c_contig, nb::device::cpu> x,
         unsigned long quality) {
