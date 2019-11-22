--- runtime/druntime/src/core/sys/dragonflybsd/dlfcn.d	2019-11-18 23:23:05.584916000 -0800
+++ runtime/druntime/src/core/sys/dragonflybsd/dlfcn.d	2019-11-18 23:24:01.974571000 -0800
@@ -90,8 +90,10 @@
 /* XSI functions first. */
 static assert(is(typeof(&dlclose) == __externC!(int, void*)));
 static assert(is(typeof(&dlerror) == __externC!(char*)));
-static assert(is(typeof(&dlopen)  == __externC!(void*, const char*, int)));
-static assert(is(typeof(&dlsym)   == __externC!(void*, void*, const char*)));
+//static assert(is(typeof(&dlopen)  == __externC!(void*, const char*, int)));
+static assert(is(typeof(&dlopen) == void* function(scope const(char*), int) nothrow @nogc));
+//static assert(is(typeof(&dlsym)   == __externC!(void*, void*, const char*)));
+static assert(is(typeof(&dlsym)   == void* function(void*, scope const(char*)) nothrow @nogc));
 
 void*    fdlopen(int, int);
 int      dladdr(const(void)*, Dl_info*);
