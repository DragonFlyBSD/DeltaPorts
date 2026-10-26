--- signals/signals_unix.go.orig	2026-10-26 03:59:36 UTC
+++ signals/signals_unix.go
@@ -1,8 +1,8 @@
 // Copyright IBM Corp. 2014, 2025
 // SPDX-License-Identifier: MPL-2.0
 
-//go:build linux || darwin || freebsd || openbsd || solaris || netbsd || aix
-// +build linux darwin freebsd openbsd solaris netbsd aix
+//go:build linux || darwin || freebsd || openbsd || solaris || netbsd || dragonfly || aix
+// +build linux darwin freebsd openbsd solaris netbsd dragonfly aix
 
 package signals
 
