#ifndef TEST_MAIN_H
#define TEST_MAIN_H
#include <stdint.h>
typedef struct { int unused; } UART_HandleTypeDef;
#define HAL_MAX_DELAY UINT32_MAX
#define DWM_RSTN_GPIO_Port 0
#define DWM_RSTN_Pin 1
#define SPI1_NCS_GPIO_Port 0
#define SPI1_NCS_Pin 2
#define DWM_IRQ_Pin 1
#define DWM_IRQ_EXTI_IRQn 0
#define GPIO_PIN_RESET 0
#define GPIO_PIN_SET 1
#define __HAL_GPIO_EXTI_CLEAR_IT(pin) ((void)(pin))
#define UNUSED(x) ((void)(x))
uint32_t __get_PRIMASK(void);
void __disable_irq(void);
void __enable_irq(void);
uint32_t NVIC_GetEnableIRQ(int irq);
void HAL_NVIC_DisableIRQ(int irq);
void HAL_NVIC_EnableIRQ(int irq);
void HAL_NVIC_ClearPendingIRQ(int irq);
void HAL_GPIO_WritePin(int port, int pin, int state);
void HAL_Delay(uint32_t ms);
uint32_t HAL_GetTick(void);
int HAL_UART_Transmit(UART_HandleTypeDef *uart, uint8_t *buf, uint16_t len, uint32_t timeout);
#endif
