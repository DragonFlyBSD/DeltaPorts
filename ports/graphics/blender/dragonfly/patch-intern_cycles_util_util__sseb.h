--- intern/cycles/util/util_sseb.h.orig	2017-09-12 03:44:17.000000000 +0000
+++ intern/cycles/util/util_sseb.h
@@ -116,7 +116,7 @@ __forceinline const sseb unpacklo( const
 __forceinline const sseb unpackhi( const sseb& a, const sseb& b ) { return _mm_unpackhi_ps(a, b); }
 
 template<size_t i0, size_t i1, size_t i2, size_t i3> __forceinline const sseb shuffle( const sseb& a ) {
-	return _mm_shuffle_epi32(a, _MM_SHUFFLE(i3, i2, i1, i0));
+	return _mm_castsi128_ps(_mm_shuffle_epi32(a, _MM_SHUFFLE(i3, i2, i1, i0)));
 }
 
 template<> __forceinline const sseb shuffle<0, 1, 0, 1>( const sseb& a ) {
