class SoftDeletingScope:
    def apply(self, builder):
        # calls .extend() on an untyped value -> fuzzy unique-name bind to the
        # unrelated DynamoDbFailedJobProvider.extend in another component
        return builder.extend(builder)
