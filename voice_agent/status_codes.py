# Successful transfer to specialist
DISPOSITION_TRANSFERRED = "XFER"  # Transferred calls - Customer qualified and transferred to debt specialist

# Qualified but disconnected
DISPOSITION_DEBT_7K_10K_HANGUP = "815K"  # Debt is between $7k-$10k but customer hung up before transfer
DISPOSITION_DEBT_OVER_10K_HANGUP = "DOK"  # Debt is over $10k but customer hung up before transfer

# Connection issues
DISPOSITION_IMMEDIATE_HANGUP = "HU"  # Call connects but customer hangs up in less than 10 seconds
DISPOSITION_NOT_INTERESTED = "NIBP"  # Customer hangs up while/after listening to the pitch or says not interested
DISPOSITION_LINE_BUSY = "BUSY"  # Number is busy
DISPOSITION_DEAD_AIR = "DAIR"  # No voice or silence for 10 seconds or more

# Contact restrictions
DISPOSITION_DO_NOT_CALL = "DNC"  # Do not call - Customer requested to be removed from list

# Qualified but not transferred
DISPOSITION_QUALIFIED_NOT_TRANSFERRED = "HL"  # Customer agrees they have debts over $7k but call is not transferred

# Not qualified reasons
DISPOSITION_LANGUAGE_BARRIER = "LB"  # Language barrier - Unable to communicate with customer
DISPOSITION_NOT_QUALIFIED = "NQ"  # Debt is not qualified - Does not meet minimum debt requirements
DISPOSITION_NO_DEBT = "ND"  # No debts - Customer has no outstanding debts
DISPOSITION_WRONG_NUMBER = "WN"  # Wrong number - Reached incorrect contact

# Follow-up actions
DISPOSITION_CALLBACK_SCHEDULED = "CALLBK"  # Callback scheduled - Customer requested a callback
DISPOSITION_NEW_LEAD = "NEW"  # Data yet to be dialed - Fresh lead not yet contacted
