#include "utils.h"

#ifdef PRODUCT_A
void app_init(void) {
    device_info_t dev;
    init_device(&dev);
    log_message("Product A initialized");
}
#else
void app_init(void) {
    device_info_t dev;
    init_device(&dev);
    simple_log("Generic init");
}
#endif

int main(void) {
    app_init();
    return 0;
}
