import random
result = ""
order = []
for i in range(400):
    order.append(f"{i+2}/{i+1}+")

random.shuffle(order)
print("".join(order))
