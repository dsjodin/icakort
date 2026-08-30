"""GraphQL-frågor mot Kivras BFF.

Fälten här speglar Kivras interna schema. Om Kivra ändrar schemat är det
den här filen som behöver uppdateras -- inget annat i projektet känner till
formen på deras data.
"""

RECEIPTS_QUERY = """
query Receipts($search: String, $limit: Int, $offset: Int) {
  receiptsV2(search: $search, limit: $limit, offset: $offset) {
    __typename
    total
    offset
    limit
    list {
      __typename
      key
      purchaseDate
      totalAmount {
        formatted
      }
      attributes {
        isCopy
        isExpensed
        isReturn
        isTrashed
      }
      store {
        name
      }
      accessInfo {
        owner {
          isMe
          name
        }
      }
    }
  }
}
"""

RECEIPT_DETAILS_QUERY = """
query ReceiptDetails($key: String!) {
  receiptV2(key: $key) {
    key
    content {
      header {
        totalPurchaseAmount
        subAmounts
        isoDate
        formattedDate
        text
      }
      items {
        allItems {
          text
          items {
            __typename
            text
            type
            ... on ProductListItem {
              ...productFields
            }
            ... on GeneralDepositListItem {
              money {
                formatted
              }
              isRefund
              description
              text
            }
            ... on GeneralDiscountListItem {
              money {
                formatted
              }
              isRefund
              text
            }
            ... on GeneralModifierListItem {
              money {
                formatted
              }
              isRefund
              text
            }
          }
        }
        noBonusItems {
          text
          items {
            __typename
            type
            ... on ProductListItem {
              ...productFields
            }
          }
        }
        returnedItems {
          text
          items {
            __typename
            type
            ... on ProductReturnListItem {
              name
              money {
                formatted
              }
              quantityCost {
                formatted
              }
              deposits {
                description
                money {
                  formatted
                }
                isRefund
              }
              costModifiers {
                description
                money {
                  formatted
                }
                isRefund
              }
              identifiers
              text
            }
          }
        }
      }
      storeInformation {
        text
        storeInformation {
          property
          value
        }
      }
      paymentInformation {
        text
        totals {
          text
          totals {
            property
            value
            subRows {
              property
              value
            }
          }
        }
      }
    }
    sender {
      name
      key
    }
    attributes {
      isUpdatedWithReturns
    }
  }
}

fragment productFields on ProductListItem {
  name
  money {
    formatted
  }
  quantityCost {
    formatted
  }
  deposits {
    description
    money {
      formatted
    }
    isRefund
  }
  costModifiers {
    description
    money {
      formatted
    }
    isRefund
  }
  identifiers
  text
}
"""
