--- vendor/github.com/10gen/openssl/build.go.intermediate	2020-05-26 16:49:31 UTC
+++ vendor/github.com/10gen/openssl/build.go
@@ -21,5 +21,6 @@ package openssl
 // #cgo windows CFLAGS: -DWIN32_LEAN_AND_MEAN -I"c:/openssl/include"
 // #cgo windows LDFLAGS: -lssleay32 -llibeay32 -lcrypt32 -L "c:/openssl/bin"
 // #cgo freebsd LDFLAGS: -lssl -lcrypto
+// #cgo dragonfly LDFLAGS: -lssl -lcrypto
 // #cgo darwin LDFLAGS: -framework CoreFoundation -framework Foundation -framework Security
 import "C"
