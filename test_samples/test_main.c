#include <stdio.h>

#define ENABLED_FEATURE

int main() {
    printf("always here\n");

#ifdef ENABLED_FEATURE
    printf("feature code\n");
#else
    printf("fallback code\n");
#endif

#ifdef DISABLED_FEATURE
    printf("this should be pruned\n");
#else
    printf("alternative path\n");
#endif

    return 0;
}