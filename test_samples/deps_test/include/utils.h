#ifndef UTILS_H
#define UTILS_H

#include "types.h"

#ifdef PRODUCT_A
void log_message(const char *msg);
#else
void simple_log(const char *msg);
#endif

int parse_config(const char *path);

#endif
