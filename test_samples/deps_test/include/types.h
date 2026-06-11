#ifndef TYPES_H
#define TYPES_H

#ifdef PRODUCT_A
typedef struct {
    int id;
    char name[32];
} device_info_t;
#else
typedef struct {
    int id;
} device_info_t;
#endif

void init_device(device_info_t *dev);

#endif
