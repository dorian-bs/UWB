#ifndef TEST_USBD_CDC_H
#define TEST_USBD_CDC_H
#include "main.h"
#define USBD_OK 0
#define USBD_BUSY 1
#define USBD_STATE_CONFIGURED 3
#define CDC_SEND_ENCAPSULATED_COMMAND 0
#define CDC_GET_ENCAPSULATED_RESPONSE 1
#define CDC_SET_COMM_FEATURE 2
#define CDC_GET_COMM_FEATURE 3
#define CDC_CLEAR_COMM_FEATURE 4
#define CDC_SET_LINE_CODING 5
#define CDC_GET_LINE_CODING 6
#define CDC_SET_CONTROL_LINE_STATE 7
#define CDC_SEND_BREAK 8
typedef struct { volatile uint32_t TxState; } USBD_CDC_HandleTypeDef;
typedef struct { void *pClassData; int dev_state; } USBD_HandleTypeDef;
typedef struct {
    int8_t (*Init)(void);
    int8_t (*DeInit)(void);
    int8_t (*Control)(uint8_t, uint8_t *, uint16_t);
    int8_t (*Receive)(uint8_t *, uint32_t *);
    int8_t (*TransmitCplt)(uint8_t *, uint32_t *, uint8_t);
} USBD_CDC_ItfTypeDef;
void USBD_CDC_SetTxBuffer(USBD_HandleTypeDef *, uint8_t *, uint16_t);
void USBD_CDC_SetRxBuffer(USBD_HandleTypeDef *, uint8_t *);
uint8_t USBD_CDC_TransmitPacket(USBD_HandleTypeDef *);
void USBD_CDC_ReceivePacket(USBD_HandleTypeDef *);
#endif
