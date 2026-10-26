--- vendor/golang.design/x/clipboard/clipboard_bsd.go.orig	2026-10-26 05:12:15 UTC
+++ vendor/golang.design/x/clipboard/clipboard_bsd.go
@@ -9,7 +9,7 @@
 // shares the same pure-Go backends and is included on a best-effort basis, but
 // it is not covered by CI and has not been runtime-tested.
 
-//go:build (openbsd || freebsd || netbsd) && !android
+//go:build (openbsd || (freebsd || dragonfly) || netbsd) && !android
 
 package clipboard
 
