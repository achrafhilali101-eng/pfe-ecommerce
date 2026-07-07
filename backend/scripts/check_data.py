import pandas as pd

products = pd.read_csv('../../data/processed/products_clean.csv')
interactions = pd.read_csv('../../data/processed/interactions.csv')

print('=== PRODUITS ===')
print(products.head(3))
print('Prix min/max/moyen:', products['price'].min(), products['price'].max(), products['price'].mean())
print()
print('=== INTERACTIONS ===')
print(interactions['interaction_type'].value_counts())
print('Utilisateurs uniques:', interactions['user_ref'].nunique())