--- Makefile.PL.orig	2008-03-06 10:12:17 UTC
+++ Makefile.PL
@@ -1,7 +1,7 @@
 use inc::Module::Install;
 
 my $iswindows = 0;
-$iswindows = 1 if $^O =~ /win32/i;
+$iswindows = 1 if $^O =~ /win32|dragonfly/i;
 
 name           'Archive-Rar';
 all_from       'lib/Archive/Rar.pm';
