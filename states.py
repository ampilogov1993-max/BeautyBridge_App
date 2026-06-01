from enum import Enum


class BotState(str, Enum):
    START = "START"
    WAITING_PAYMENT = "WAITING_PAYMENT"
    BOOKED_PENDING_PAYMENT = "BOOKED_PENDING_PAYMENT"
    BOOKED_CONFIRMED = "BOOKED_CONFIRMED"
    WAITING_ADMIN_CONFIRMATION = "WAITING_ADMIN_CONFIRMATION"


STATE_TRANSITIONS = {
    BotState.START: [
        BotState.WAITING_PAYMENT,
        BotState.WAITING_ADMIN_CONFIRMATION,
    ],
    BotState.WAITING_PAYMENT: [
        BotState.BOOKED_CONFIRMED,
        BotState.WAITING_ADMIN_CONFIRMATION,
    ],
    BotState.BOOKED_PENDING_PAYMENT: [
        BotState.BOOKED_CONFIRMED,
        BotState.WAITING_ADMIN_CONFIRMATION,
    ],
    BotState.BOOKED_CONFIRMED: [BotState.START],
    BotState.WAITING_ADMIN_CONFIRMATION: [BotState.START],
}


def can_transition(from_state, to_state):
    return to_state in STATE_TRANSITIONS.get(from_state, [])


def next_states(current):
    return STATE_TRANSITIONS.get(current, [])
